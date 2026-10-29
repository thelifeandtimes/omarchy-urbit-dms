import copy
from datetime import datetime, timedelta, timezone
import http.server
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from instance import CANONICAL_ID, STATE_NAME, Instance, load_instance
from connection_offer import HandoffError, decode
from application_connection import accept
from session_store import SessionStore, StoreError
RECEIVER = Path(__file__).resolve().parent / 'application_connection.py'


SECRET = '0v.private-handoff-cookie'


class Ship(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:' + str(self.server_port)
        self.identity = '~zod'
        self.redirect = False
        self.requests = []
        self.sessions = {SECRET, '0v.another-plugin'}
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.server.requests.append((self.path, self.headers.get('Cookie')))
        cookie = self.headers.get('Cookie', '').removeprefix('urbauth-~zod=')
        status = 200 if self.path == '/~/host' or cookie in self.server.sessions else 403
        if self.server.redirect:
            status = 302
        self.send_response(status)
        if status == 302:
            self.send_header('Location', self.server.url + '/would-leak')
        self.end_headers()
        self.wfile.write(self.server.identity.encode())

    def do_POST(self):
        self.server.requests.append((self.path, self.headers.get('Cookie')))
        if self.path != '/~/logout' or self.rfile.read(int(self.headers.get('Content-Length', '0'))):
            self.send_error(400)
            return
        cookie = self.headers.get('Cookie', '').removeprefix('urbauth-~zod=')
        self.server.sessions.discard(cookie)
        self.send_response(303)
        self.send_header('Location', '/must-not-follow')
        self.end_headers()


class KeyService:
    def __init__(self):
        self.values = {}
        self.calls = []
        self.locked = False

    def call(self, args, data):
        self.calls.append(args)
        if self.locked:
            return 1, b''
        key = (args[args.index('application') + 1], args[-1])
        action = args[1]
        if action == 'store':
            self.values[key] = data
            return 0, b''
        if action == 'clear':
            self.values.pop(key, None)
            return 0, b''
        return (0, self.values[key]) if key in self.values else (1, b'')

    def theme(self, args, data=b'', **kwargs):
        return self.call(args, data)

    def dms(self, args, input=b'', **kwargs):
        code, output = self.call(args, input)
        return subprocess.CompletedProcess(args, code, output)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ship = Ship()
        self.addCleanup(self.ship.close)
        self.keys = KeyService()
        self.instance = Instance(CANONICAL_ID + '.auth-lab', 'auth-lab', STATE_NAME + '.auth-lab', 'Auth Lab')
        self.store = SessionStore(self.root / self.instance.state_name, runner=self.keys.dms, instance=self.instance)
        self.offer = {'version': 1, 'request_id': '1' * 32, 'connection_id': '2' * 32,
                      'plugin_id': self.instance.plugin_id, 'profile': self.instance.profile,
                      'session': {'origin': self.ship.url, 'ship': '~zod', 'cookie_name': 'urbauth-~zod',
                                  'cookie_value': SECRET, 'expires': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace('+00:00', 'Z')}}

    def receive(self, offer=None):
        raw = json.dumps(offer if offer is not None else self.offer).encode()
        return accept(raw, store=self.store, instance=self.instance)

    def saved(self):
        return self.store.lookup(self.ship.url, "~zod")

    def test_accepted_only_after_identity_and_private_storage_without_publishing(self):
        self.keys.values[(STATE_NAME, self.ship.url)] = b'production-sentinel'
        reply = self.receive()
        self.assertEqual(reply['status'], 'accepted')
        self.assertEqual(reply['request_id'], self.offer['request_id'])
        self.assertEqual(self.saved()['cookieValue'], SECRET)
        self.assertEqual(self.keys.values[(STATE_NAME, self.ship.url)], b'production-sentinel')
        self.assertEqual(self.ship.requests, [('/~/host', None), ('/~/name', 'urbauth-~zod=' + SECRET)])
        for path in self.store.root.glob('*.json'):
            self.assertNotIn(SECRET.encode(), path.read_bytes())
        self.assertNotIn(SECRET, json.dumps(reply))
        self.assertNotIn(SECRET, str(self.keys.calls))
        self.assertEqual(self.store.account()["ship"], "~zod")

    def test_refuses_replacing_an_existing_account(self):
        self.receive()
        previous = copy.deepcopy(self.keys.values)
        offer = copy.deepcopy(self.offer)
        offer['request_id'] = '3' * 32
        offer['session']['cookie_value'] = '0v.another-plugin'
        with self.assertRaises(HandoffError):
            self.receive(offer)
        self.assertEqual(self.keys.values, previous)

    def test_wrong_profile_malformed_and_expired_offers_do_not_reach_network_or_keyring(self):
        invalid = []
        for key, value in [('profile', 'default'), ('plugin_id', CANONICAL_ID), ('version', True), ('request_id', SECRET), ('extra', SECRET)]:
            bad = copy.deepcopy(self.offer)
            bad[key] = value
            invalid.append(bad)
        for key, value in [('origin', self.ship.url + '/path'), ('origin', 'http://0.0.0.0'), ('cookie_name', 'urbauth-~nec'),
                           ('cookie_value', 'cookie;inject=x'), ('expires', '2000-01-01T00:00:00Z'), ('expires', 'bad')]:
            bad = copy.deepcopy(self.offer)
            bad['session'][key] = value
            invalid.append(bad)
        for offer in invalid:
            with self.subTest(offer=offer.keys()), self.assertRaises(HandoffError):
                self.receive(offer)
        self.assertEqual(self.ship.requests, [])
        self.assertEqual(self.keys.calls, [])
        with self.assertRaises(HandoffError):
            decode(b'{"version":1,"version":1}', self.instance)
        with self.assertRaises(HandoffError):
            decode(b' ' * 16385, self.instance)

    def test_rfc3339_offset_is_accepted_without_extending_expiry(self):
        from datetime import timezone
        expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        self.offer['session']['expires'] = expiry.astimezone(timezone(timedelta(hours=-7))).isoformat()
        self.assertEqual(self.receive()['status'], 'accepted')
        self.assertEqual(datetime.fromisoformat(self.saved()['expires']), expiry)

    def test_identity_mismatch_never_sends_cookie_or_persists(self):
        self.ship.identity = '~nec'
        with self.assertRaises(HandoffError):
            self.receive()
        self.assertEqual(self.ship.requests, [('/~/host', None)])
        self.assertEqual(self.keys.calls, [])

    def test_redirects_are_not_followed(self):
        self.ship.redirect = True
        with self.assertRaises(HandoffError):
            self.receive()
        self.assertEqual(self.ship.requests, [('/~/host', None)])
        self.assertEqual(self.keys.calls, [])

    def test_locked_keyring_does_not_acknowledge_or_create_account(self):
        self.keys.locked = True
        with self.assertRaises(StoreError):
            self.receive()
        rows = json.loads((self.store.root / 'application-attempts.json').read_text())['attempts']
        self.assertEqual(rows[0]['phase'], 'pending')
        self.assertFalse((self.store.root / 'account.json').exists())
        self.assertFalse((self.store.root / 'state.json').exists())

    def test_saved_expiry_is_enforced_on_later_lookup(self):
        self.receive()
        key = (self.instance.state_name, self.ship.url)
        session = json.loads(self.keys.values[key])
        session['expires'] = '2000-01-01T00:00:00Z'
        self.keys.values[key] = json.dumps(session).encode()
        with self.assertRaises(StoreError):
            self.saved()

    def test_cli_rejection_cannot_echo_an_injected_secret(self):
        result = subprocess.run([sys.executable, '-B', str(RECEIVER)], input=json.dumps({'version': SECRET}).encode(),
                                capture_output=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['status'], 'rejected')
        self.assertNotIn(SECRET.encode(), result.stdout + result.stderr)

    def test_bridge_refuses_redirects_and_ignores_proxy_environment(self):
        import urllib.error
        from bridge import Bridge
        from session_store import cookie_for
        self.receive()
        with patch.dict(os.environ, {'http_proxy': 'http://127.0.0.1:1', 'no_proxy': '', 'NO_PROXY': ''}):
            from outbound_journal import OutboundJournal
            bridge = Bridge(session_store=self.store,outbound_journal=OutboundJournal(self.store.root))
            self.addCleanup(bridge.release_transport_owner)
            self.addCleanup(bridge.pool.shutdown, wait=False)
            bridge.base = self.ship.url
            bridge.cookies.set_cookie(cookie_for(self.saved()))
            self.assertEqual(bridge.request('/~/name'), b'~zod')
            self.ship.redirect = True
            self.ship.requests.clear()
            with self.assertRaises(urllib.error.HTTPError) as caught:
                bridge.request('/~/name')
            self.assertEqual(caught.exception.code, 302)
            caught.exception.close()
        self.assertEqual(self.ship.requests, [('/~/name', 'urbauth-~zod=' + SECRET)])

    def test_bridge_remote_logout_revokes_only_its_saved_session(self):
        from bridge import Bridge
        self.receive()
        from outbound_journal import OutboundJournal
        bridge = Bridge(session_store=self.store,outbound_journal=OutboundJournal(self.store.root))
        self.addCleanup(bridge.release_transport_owner)
        self.addCleanup(bridge.pool.shutdown, wait=False)
        self.assertEqual(bridge.logout(), 'revoked')
        self.assertNotIn(SECRET, self.ship.sessions)
        self.assertIn('0v.another-plugin', self.ship.sessions)
        self.assertIsNone(self.store.account())
        self.assertNotIn('/must-not-follow', [path for path, _ in self.ship.requests])

    def test_unconfirmed_remote_logout_retains_credentials_for_retry(self):
        from bridge import Bridge
        self.receive()
        self.ship.identity = '~nec'
        from outbound_journal import OutboundJournal
        bridge = Bridge(session_store=self.store,outbound_journal=OutboundJournal(self.store.root))
        self.addCleanup(bridge.release_transport_owner)
        self.addCleanup(bridge.pool.shutdown, wait=False)
        with self.assertRaises(StoreError):
            bridge.logout()
        self.assertEqual(self.saved()['cookieValue'], SECRET)
        self.assertIsNotNone(self.store.account())
        self.assertIn(SECRET, self.ship.sessions)

    def test_expired_local_session_can_still_be_logged_out(self):
        from bridge import Bridge
        self.receive()
        key = (self.instance.state_name, self.ship.url)
        session = json.loads(self.keys.values[key])
        session['expires'] = '2000-01-01T00:00:00Z'
        self.keys.values[key] = json.dumps(session).encode()
        from outbound_journal import OutboundJournal
        bridge = Bridge(session_store=self.store,outbound_journal=OutboundJournal(self.store.root))
        self.addCleanup(bridge.release_transport_owner)
        self.addCleanup(bridge.pool.shutdown, wait=False)
        self.assertEqual(bridge.logout(), 'revoked')
        self.assertIsNone(self.store.account())

    def test_expiry_is_carried_into_the_cookie_jar(self):
        from session_store import cookie_for
        self.receive()
        cookie = cookie_for(self.saved())
        self.assertEqual(cookie.expires, int(datetime.fromisoformat(self.offer['session']['expires']).timestamp()))
        self.assertFalse(cookie.discard)

    def test_competing_account_writers_cannot_replace_the_receiver_account(self):
        from connection_offer import plugin_session
        other = SessionStore(self.store.root, runner=self.keys.dms, instance=self.instance)
        with self.store.locked(), self.assertRaises(StoreError):
            other.save_new(plugin_session(self.offer['session']))
        self.assertEqual(self.keys.calls, [])



class InstanceTests(unittest.TestCase):
    def test_profile_requires_matching_manifest_and_separates_state_and_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'manifest.json').write_text(json.dumps({'id': CANONICAL_ID}))
            stable = load_instance(root)
            self.assertEqual((stable.plugin_id, stable.state_name), (CANONICAL_ID, STATE_NAME))
            (root / 'application-profile.json').write_text('{"version":1,"profile":"auth-lab"}')
            with self.assertRaises(ValueError):
                load_instance(root)
            (root / 'manifest.json').write_text(json.dumps({'id': CANONICAL_ID + '.auth-lab'}))
            lab = load_instance(root)
            self.assertNotEqual(lab.state_name, stable.state_name)
            self.assertIn('auth-lab', lab.label)
            (root / 'application-profile.json').unlink()
            (root / 'application-profile.json').symlink_to(root / 'manifest.json')
            with self.assertRaises(OSError):
                load_instance(root)


if __name__ == '__main__':
    unittest.main()
