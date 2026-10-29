"""Unknown send outcomes survive process/profile reload and never trigger replay."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from bridge import Bridge
from outbound_journal import OutboundJournal
from session_store import StoreError


class OutboundTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'stable'
        self.journal=OutboundJournal(self.root)
        self.bridge=Bridge(session_store=object(),outbound_journal=self.journal)
        self.addCleanup(lambda:self.bridge.pool.shutdown(wait=True,cancel_futures=True))
        self.bridge.base='http://127.0.0.1:18081';self.bridge.ship='~zod';self.bridge.channel='original'
        self.bridge.pool.submit=lambda *a,**kw:None
        self.events=[];self.calls=[]
        self.bridge.emit=lambda event,**fields:self.events.append((event,fields))

    def dispatch(self,receipt=None,raise_after=False):
        def request(path,data,method):
            command=json.loads(data)[0]
            # Durable uncertainty must already be readable by a fresh helper.
            self.assertEqual(len(OutboundJournal(self.root).unresolved()),1)
            self.calls.append(command)
            if raise_after:raise OSError('response lost')
            self.bridge.on_fact({'id':command['id'],'response':'poke',**(receipt or {})},None)
        self.bridge.request=request

    def test_unknown_send_is_restored_without_replay_and_keeps_private_draft(self):
        self.dispatch()
        with self.assertRaisesRegex(ValueError,'status unknown'):
            self.bridge.send('~nec','a private draft',1)
        restored=OutboundJournal(self.root)
        rows=restored.unresolved();self.assertEqual(len(rows),1)
        self.assertNotIn('text',json.dumps(rows));self.assertEqual(len(self.calls),1)
        content=json.loads(restored.path.read_text());self.assertEqual(content['messages'][0]['message']['text'],'a private draft')
        self.assertEqual(restored.path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777,0o700)
        new_bridge=Bridge(session_store=object(),outbound_journal=restored)
        self.addCleanup(lambda:new_bridge.pool.shutdown(wait=True,cancel_futures=True))
        new_bridge.emit=lambda event,**fields:self.events.append((event,fields))
        new_bridge.request=lambda *a: self.fail('reload must not send anything')
        new_bridge.emit_outbound()
        self.assertEqual(self.events[-1][1]['unresolved'],rows)
        self.assertEqual(self.bridge.pending,{})

    def test_lost_http_reply_stays_uncertain(self):
        self.dispatch(raise_after=True)
        with self.assertRaises(OSError):self.bridge.send('~nec','possibly delivered',2)
        self.assertEqual(len(OutboundJournal(self.root).unresolved()),1)
        self.assertEqual(len(self.calls),1);self.assertEqual(self.bridge.pending,{})

    def test_positive_receipt_or_explicit_rejection_resolves_intent(self):
        self.dispatch({'ok':'ok'});self.bridge.send('~nec','delivered',3)
        self.assertEqual(self.journal.unresolved(),[])
        self.dispatch({'err':'rejected'})
        with self.assertRaisesRegex(ValueError,'rejected'):self.bridge.send('~nec','refused',4)
        self.assertEqual(self.journal.unresolved(),[])
        self.assertEqual([r['phase'] for r in json.loads(self.journal.path.read_text())['messages']],['acknowledged','rejected'])

    def test_disk_failure_before_dispatch_sends_nothing(self):
        self.dispatch()
        with patch.object(self.journal,'_write',side_effect=OSError('disk full')):
            with self.assertRaises(StoreError):self.bridge.send('~nec','not sent',5)
        self.assertEqual(self.calls,[]);self.assertEqual(self.bridge.pending,{})

    def test_only_exact_history_on_same_origin_and_id_resolves(self):
        self.dispatch()
        with self.assertRaises(ValueError):self.bridge.send('~nec','remember this',6)
        message=json.loads(self.journal.path.read_text())['messages'][0]['message']
        for url,ship,peer,msgs in [
            ('http://127.0.0.1:18082','~zod','~nec',[message]),
            (self.bridge.base,'~bus','~nec',[message]),
            (self.bridge.base,'~zod','~bus',[message]),
            (self.bridge.base,'~zod','~nec',[]),
            (self.bridge.base,'~zod','~nec',[dict(message,text='different')])]:
            self.journal.observe(url,ship,peer,msgs);self.assertEqual(len(self.journal.unresolved()),1)
        self.journal.observe(self.bridge.base,'~zod','~nec',[dict(message,kind='/chat')])
        self.assertEqual(self.journal.unresolved(),[]);self.assertEqual(len(self.calls),1)

    def test_unknown_version_is_preserved_and_blocks_dispatch(self):
        self.root.mkdir(mode=0o700);self.journal.path.write_text('{"version":99,"messages":[]}');self.journal.path.chmod(0o600)
        before=self.journal.path.read_bytes();self.dispatch()
        with self.assertRaises(StoreError):self.bridge.send('~nec','do not send',7)
        self.assertEqual(self.journal.path.read_bytes(),before);self.assertEqual(self.calls,[])
        self.bridge.emit_outbound();self.assertFalse(self.events[-1][1]['readable'])

    def test_profile_journals_are_independent(self):
        self.dispatch()
        with self.assertRaises(ValueError):self.bridge.send('~nec','stable only',8)
        self.assertEqual(OutboundJournal(Path(self.temp.name)/'dev').unresolved(),[])
        self.assertEqual(len(OutboundJournal(self.root).unresolved()),1)

    def test_uncertain_history_is_not_evicted_at_capacity(self):
        message={'id':'~zod/123','author':'~zod','sent':1,'text':'pending'}
        for _ in range(128):self.journal.begin(self.bridge.base,'~zod','~nec',message)
        before=self.journal.path.read_bytes()
        with self.assertRaisesRegex(StoreError,'unresolved'):self.journal.begin(self.bridge.base,'~zod','~nec',message)
        self.assertEqual(self.journal.path.read_bytes(),before)

    def test_symlink_journal_is_refused_without_touching_target(self):
        target=Path(self.temp.name)/'target';target.write_text('preserve')
        self.root.mkdir(mode=0o700);self.journal.path.symlink_to(target)
        self.dispatch()
        with self.assertRaises(StoreError):self.bridge.send('~nec','not sent',9)
        self.assertEqual(target.read_text(),'preserve');self.assertEqual(self.calls,[])

    def test_failed_resolution_still_leaves_unknown_after_positive_receipt(self):
        self.dispatch({'ok':'ok'})
        with patch.object(self.journal,'finish',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):self.bridge.send('~nec','delivered but unsaved receipt',10)
        self.assertEqual(len(OutboundJournal(self.root).unresolved()),1)
        self.assertFalse(any(event=='sent' for event,_ in self.events))
        self.assertEqual(len(self.calls),1)
