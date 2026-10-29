import copy
import json
import subprocess
import unittest
from unittest.mock import patch

import test_connection as fixtures
from test_connection import SECRET, STATE_NAME
from application_connection import reconcile
from connection_journal import Journal, secret_present
from connection_offer import HandoffError


class Crash(BaseException):
    pass


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.ConnectionTests.setUp
    receive = fixtures.ConnectionTests.receive
    saved = fixtures.ConnectionTests.saved

    def request(self):
        return {k: self.offer[k] for k in ('version', 'request_id', 'connection_id', 'plugin_id', 'profile')} | {
            'action': 'reconcile', 'origin': self.ship.url, 'ship': '~zod'}

    def present(self, instance, origin):
        if self.keys.locked:
            raise HandoffError('keyring-unavailable')
        return (instance.state_name, origin) in self.keys.values

    def recover(self, value=None):
        args = dict(store=self.store, instance=self.instance, present=self.present)
        pass
        return reconcile(json.dumps(value or self.request()), **args)

    def account_exists(self):
        return self.store.account() is not None

    def test_recovery_removes_only_its_account_and_is_idempotent(self):
        self.receive()
        self.keys.values[(STATE_NAME, self.ship.url)] = b'production-sentinel'
        self.ship.sessions.discard(SECRET)  # Issuer has already revoked it.
        reply = self.recover()
        self.assertEqual(reply['status'], 'reconciled')
        self.assertEqual(reply['outcome'], 'removed')
        self.assertFalse(self.account_exists())
        self.assertNotIn((self.instance.state_name, self.ship.url), self.keys.values)
        self.assertEqual(self.keys.values[(STATE_NAME, self.ship.url)], b'production-sentinel')
        self.assertEqual(self.recover(), reply)
        self.assertNotIn(SECRET, json.dumps(reply))
        for path in self.store.root.glob('*.json'):
            self.assertNotIn(SECRET.encode(), path.read_bytes())

    def test_new_account_on_another_origin_preserves_it_and_cleans_old_credential(self):
        self.receive()
        old_key = (self.instance.state_name, self.ship.url)
        other = {'url': 'https://other.example', 'ship': '~nec', 'cookieName': 'urbauth-~nec', 'cookieValue': '0v.other-origin'}
        self.store.save(other)
        self.assertEqual(self.recover()['outcome'], 'superseded')
        self.assertEqual(self.store.account()['url'], other['url'])
        self.assertEqual(self.store.lookup(other['url'], other['ship']), other)
        self.assertNotIn(old_key, self.keys.values)

    def test_wrong_attempt_binding_preserves_everything(self):
        self.receive()
        previous = copy.deepcopy(self.keys.values)
        request = self.request()
        request['connection_id'] = 'f' * 32
        with self.assertRaises(HandoffError):
            self.recover(request)
        self.assertTrue(self.account_exists())
        self.assertEqual(self.keys.values, previous)

    def test_locked_keyring_keeps_account_and_pending_recovery(self):
        self.receive()
        self.keys.locked = True
        with self.assertRaises(HandoffError):
            self.recover()
        self.assertTrue(self.account_exists())
        self.keys.locked = False
        self.assertEqual(self.recover()['outcome'], 'removed')

    def test_absent_token_is_not_a_locked_keyring(self):
        self.receive()
        self.keys.values.pop((self.instance.state_name, self.ship.url))
        self.assertEqual(self.recover()['outcome'], 'removed')
        self.assertFalse(self.account_exists())

    def test_newer_standalone_account_is_preserved(self):
        self.receive()
        session = self.saved()
        session['cookieValue'] = '0v.new-standalone'
        self.store.save(session)
        previous = copy.deepcopy(self.keys.values)
        self.assertEqual(self.recover()['outcome'], 'superseded')
        self.assertEqual(self.keys.values, previous)
        self.assertTrue(self.account_exists())

    def test_keyring_write_without_account_commit_can_be_recovered(self):
        original = Journal.save
        # Beginning the journal is durable before the keyring can be touched.
        def crash_after_begin(journal):
            original(journal)
            if journal.entries[-1]['phase'] == 'pending':
                raise Crash()
        with patch.object(Journal, 'save', crash_after_begin), self.assertRaises(Crash):
            self.receive()
        self.assertEqual(self.recover()['outcome'], 'absent')
        # A distinct attempt reaches the keyring then fails its account write.
        self.offer['request_id'] = '3' * 32
        import os
        replace = os.replace
        def fail_account(source, target):
            if str(target).endswith('/account.json'):
                raise Crash()
            return replace(source, target)
        with patch('session_store.os.replace', side_effect=fail_account), self.assertRaises(Crash):
            self.receive()
        self.assertIn((self.instance.state_name, self.ship.url), self.keys.values)
        self.assertEqual(self.recover()['outcome'], 'absent')
        self.assertFalse(self.account_exists())

    def test_recovery_after_keyring_clear_and_after_account_removal(self):
        self.receive()
        original = self.keys.call
        def crash_clear(args, data):
            result = original(args, data)
            if args[1] == 'clear':
                raise Crash()
            return result
        with patch.object(self.keys, 'call', crash_clear), self.assertRaises(Crash):
            self.recover()
        self.assertTrue(self.account_exists())
        with patch.object(Journal, 'finish', side_effect=Crash()), self.assertRaises(Crash):
            self.recover()
        self.assertFalse(self.account_exists())
        self.assertEqual(self.recover()['status'], 'reconciled')

    def test_duplicate_ack_loss_never_erases_a_later_account(self):
        self.receive()
        self.recover()
        session = {'url': self.ship.url, 'ship': '~zod', 'cookieName': 'urbauth-~zod', 'cookieValue': '0v.new-standalone'}
        self.store.save(session)
        previous = copy.deepcopy(self.keys.values)
        self.assertEqual(self.recover()['status'], 'reconciled')
        self.assertEqual(self.keys.values, previous)
        self.assertTrue(self.account_exists())


class SecretMetadataTests(unittest.TestCase):
    def test_locked_duplicate_missing_and_available_are_distinct(self):
        instance = type('Instance', (), {'state_name': 'fixture.auth-lab'})()
        for data, expected in [([[], []], False), ([['/item/one'], []], True),
                               ([[], ['/item/locked']], None), ([['/item/a', '/item/b'], []], None)]:
            def run(args, **kwargs):
                self.assertIn('SearchItems', args)
                self.assertNotIn('GetSecrets', args)
                return subprocess.CompletedProcess(args, 0, json.dumps({'type': 'aoao', 'data': data}).encode())
            if expected is None:
                with self.assertRaises(HandoffError):
                    secret_present(instance, 'https://example.test', runner=run)
            else:
                self.assertEqual(secret_present(instance, 'https://example.test', runner=run), expected)


if __name__ == '__main__':
    unittest.main()
