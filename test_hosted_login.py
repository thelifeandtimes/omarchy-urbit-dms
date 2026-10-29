"""Hosted-style cookies through the real cookie parser, without network or keyring."""
import http.cookiejar
import io
from email.message import Message
import unittest
import tempfile
from outbound_journal import OutboundJournal

from bridge import Bridge
from test_bridge import MemoryStore


class Response(io.BytesIO):
    def __init__(self, headers):
        super().__init__(b'')
        self.headers = Message()
        for header in headers:
            self.headers.add_header('Set-Cookie', header)

    def info(self):
        return self.headers


class HostedLoginTests(unittest.TestCase):
    url = 'https://sampel-palnet.example'

    def bridge(self, headers):
        store = MemoryStore()
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        bridge = Bridge(session_store=store,outbound_journal=OutboundJournal(temporary.name))
        self.addCleanup(bridge.release_transport_owner)
        self.addCleanup(bridge.pool.shutdown, wait=True)
        calls = []

        class Opener:
            def open(self, request, timeout):
                calls.append(request.full_url)
                response = Response(headers)
                bridge.cookies.extract_cookies(response, request)
                return response

        bridge.opener = Opener()
        bridge.scry = lambda agent, path: calls.append((agent, path)) or []
        bridge.start_session = lambda: calls.append('start-session')
        return bridge, store, calls

    def test_host_only_and_explicit_same_host_domain_restore_equivalently(self):
        for scope in ('', '; Domain=sampel-palnet.example', '; Domain=.sampel-palnet.example'):
            with self.subTest(scope=scope):
                bridge, store, calls = self.bridge([
                    'urbauth-~sampel-palnet=hosted-session; Path=/; Secure; HttpOnly' + scope,
                    '__gateway=irrelevant; Path=/; Secure',
                ])
                self.assertEqual(bridge.login(self.url, '+sample-code'), '~sampel-palnet')
                self.assertEqual(store.session['url'], self.url)
                self.assertEqual(store.session['cookieValue'], 'hosted-session')
                self.assertEqual(calls, [self.url + '/~/login', ('chat', '/dm'), 'start-session'])
                # Restored cookies use the same origin and work with urllib's real jar.
                from session_store import cookie_for
                import urllib.request
                jar = http.cookiejar.CookieJar()
                jar.set_cookie(cookie_for(store.session))
                request = urllib.request.Request(self.url + '/~/scry/chat/dm.json')
                jar.add_cookie_header(request)
                self.assertEqual(request.get_header('Cookie'), 'urbauth-~sampel-palnet=hosted-session')

    def test_ambiguous_or_wrong_path_session_is_not_saved_or_started(self):
        cases = [
            ['urbauth-~sampel-palnet=a; Path=/apps/groups; Secure'],
            ['urbauth-~sampel-palnet=a; Path=/; Secure', 'urbauth-~nec=b; Path=/; Secure'],
            ['urbauth-~sampel-palnet=a; Domain=.example; Path=/; Secure'],
            ['urbauth-~sampel-palnet=a; Max-Age=0; Path=/; Secure'],
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                bridge, store, calls = self.bridge(headers)
                with self.assertRaises(ValueError):
                    bridge.login(self.url, '+sample-code')
                self.assertIsNone(store.session)
                self.assertEqual(calls, [self.url + '/~/login'])
                self.assertFalse(bridge.ship)
                self.assertFalse(list(bridge.cookies))


if __name__ == '__main__':
    unittest.main()
