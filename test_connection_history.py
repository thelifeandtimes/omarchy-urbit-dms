import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import connection_journal as journal_module
from connection_journal import Journal
from connection_offer import HandoffError


class Crash(BaseException):
    pass


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = {'url': 'https://ship.example', 'ship': '~zod',
                        'cookieName': 'urbauth-~zod', 'cookieValue': 'synthetic-private-cookie'}
        self.offer = {'version': 1, 'request_id': 'a' * 32, 'connection_id': 'b' * 32,
                      'plugin_id': 'fixture.auth-lab', 'profile': 'auth-lab'}

    def binding(self, offer=None):
        return dict(offer or self.offer, origin=self.session['url'], ship='~zod')

    def test_more_than_64_cycles_retains_receipts_and_blocks_old_offer(self):
        first = None
        for index in range(96):
            offer = dict(self.offer, request_id=f'{index + 1:032x}')
            first = first or offer
            journal = Journal(self.root)
            row = journal.begin(offer, self.session)
            row['phase'] = 'accepted'
            journal.save()
            journal.finish(row, 'removed')
            self.assertEqual(Journal(self.root).entries, [])
        journal = Journal(self.root)
        self.assertEqual(len(list((self.root / 'application-history').glob('*.json'))), 96)
        with self.assertRaises(HandoffError):
            journal.begin(first, self.session)
        receipt = journal.find(self.binding(first))
        self.assertEqual((receipt['phase'], receipt['outcome']), ('reconciled', 'removed'))
        self.assertNotIn(self.session['cookieValue'], json.dumps(receipt))
        pending = journal.begin(self.offer, self.session)
        journal.compact()
        self.assertEqual(Journal(self.root).entries, [pending])

    def test_old_generation_cleanup_remains_compatible(self):
        spec = importlib.util.spec_from_file_location('legacy_connection_journal', Path(__file__).parent / 'testdata/connection_journal_v1.py')
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
        old = legacy.Journal(self.root)
        # A full legacy completed journal must become usable without dropping IDs.
        for index in range(64):
            offer = dict(self.offer, request_id=f'{index + 1:032x}')
            row = old.begin(offer, self.session)
            old.finish(row, 'removed')
        journal = Journal(self.root)
        pending = journal.begin(self.offer, self.session)
        self.assertEqual(len(journal.entries), 1)
        self.assertEqual(json.loads(journal.path.read_text())['version'], 1)
        # Old retained cleanup can still read/finish its remaining active record.
        old = legacy.Journal(self.root)
        self.assertEqual(old.find(self.binding()), pending)
        old.finish(old.find(self.binding()), 'removed')
        journal = Journal(self.root)
        journal.compact()
        self.assertEqual(journal.find(self.binding())['outcome'], 'removed')
        self.assertIsNone(legacy.Journal(self.root).find(self.binding()))
        with self.assertRaises(HandoffError):
            journal.begin(self.offer, self.session)

    def test_crash_after_receipt_before_active_replacement(self):
        journal = Journal(self.root)
        row = journal.begin(self.offer, self.session)
        original = Journal.save
        def crash_empty(current):
            if current.entries == []:
                raise Crash()
            return original(current)
        with patch.object(Journal, 'save', crash_empty), self.assertRaises(Crash):
            journal.finish(row, 'removed')
        reopened = Journal(self.root)
        self.assertEqual(reopened.entries[0]['phase'], 'reconciled')
        path = self.root / 'application-history' / (self.offer['request_id'] + '.json')
        before = path.read_bytes()
        reopened.compact()
        self.assertEqual(Journal(self.root).entries, [])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(Journal(self.root).find(self.binding())['outcome'], 'removed')

    def test_retry_syncs_receipt_directory_before_active_removal(self):
        journal = Journal(self.root)
        row = journal.begin(self.offer, self.session)
        sync = journal_module.sync_directory
        def crash_history(directory):
            if Path(directory).name == 'application-history':
                raise Crash()
            return sync(directory)
        with patch.object(journal_module, 'sync_directory', crash_history), self.assertRaises(Crash):
            journal.finish(row, 'removed')
        events = []
        def record_sync(directory):
            events.append(('sync', Path(directory).name))
            return sync(directory)
        save = Journal.save
        def record_save(current):
            events.append(('save', len(current.entries)))
            return save(current)
        with patch.object(journal_module, 'sync_directory', record_sync), patch.object(Journal, 'save', record_save):
            Journal(self.root).compact()
        self.assertLess(events.index(('sync', 'application-history')), events.index(('save', 0)))

    def test_wrong_binding_and_unsafe_receipt_preserve_active_evidence(self):
        journal = Journal(self.root)
        row = journal.begin(self.offer, self.session)
        journal.finish(row, 'removed')
        changed = self.binding()
        changed['connection_id'] = 'f' * 32
        with self.assertRaises(HandoffError):
            Journal(self.root).find(changed)
        path = self.root / 'application-history' / (self.offer['request_id'] + '.json')
        before = path.read_bytes()
        os.chmod(path, 0o644)
        with self.assertRaises(HandoffError):
            Journal(self.root).begin(self.offer, self.session)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(Journal(self.root).entries, [])

    def test_archive_refuses_pending_or_accepted_record(self):
        journal = Journal(self.root)
        row = journal.begin(self.offer, self.session)
        for phase in ('pending', 'accepted'):
            row['phase'] = phase
            journal.save()
            with self.assertRaises(HandoffError):
                journal.save_receipt(row)
            journal.compact()
            self.assertEqual(Journal(self.root).entries, [row])


if __name__ == '__main__':
    unittest.main()
