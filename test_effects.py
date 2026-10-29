"""Conversation effects remain uncertain across reload unless positively receipted."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from bridge import Bridge
from effects_journal import EffectsJournal
from outbound_journal import OutboundJournal
from session_store import StoreError

class EffectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.bridge=Bridge(session_store=object(),outbound_journal=OutboundJournal(self.temp.name))
        self.addCleanup(lambda:self.bridge.pool.shutdown(wait=True,cancel_futures=True))
        self.bridge.base='http://127.0.0.1:18081';self.bridge.ship='~zod';self.bridge.channel='owned'
        self.events=[];self.requests=[];self.bridge.emit=lambda name,**data:self.events.append((name,data));self.bridge.refresh=lambda:None
    def receipt(self,fields):
        def send(path,raw,method):
            body=json.loads(raw)[0]
            rows=self.bridge.effects.unresolved()
            self.assertTrue(rows,'intent must exist before dispatch')
            self.requests.append(body)
            self.bridge.on_fact({'id':body['id'],'response':'poke',**fields},None)
            return b''
        self.bridge.request=send
    def test_positive_receipts_resolve_both_effects(self):
        self.receipt({'ok':'ok'});self.bridge.unreads['~nec']=4
        self.bridge.accept('~nec');self.bridge.mark_read('~nec')
        self.assertEqual(self.bridge.effects.unresolved(),[])
        self.assertEqual(self.bridge.unreads['~nec'],0)
        self.assertEqual([r['mark'] for r in self.requests],['chat-dm-rsvp','activity-action'])
        rows=json.loads(self.bridge.effects.path.read_text())['effects']
        self.assertEqual([r['phase'] for r in rows],['acknowledged','acknowledged'])
    def test_unknown_effects_survive_reload_and_preserve_unread_count(self):
        self.receipt({'ok':'unrecognized'});self.bridge.unreads['~nec']=4
        with self.assertRaisesRegex(ValueError,'status unknown'):self.bridge.accept('~nec')
        self.bridge.mark_read('~nec')
        before=self.bridge.effects.path.read_bytes();restored=EffectsJournal(self.temp.name)
        self.assertEqual({r['kind'] for r in restored.unresolved()},{'accept-invitation','mark-read'})
        self.assertEqual(self.bridge.unreads['~nec'],4);self.assertEqual(len(self.requests),2)
        self.bridge.emit_outbound();self.assertEqual(self.events[-1][1]['unresolvedEffects'],restored.unresolved())
        self.assertEqual(self.bridge.effects.path.read_bytes(),before)
        self.assertFalse(any(n=='accepted' for n,_ in self.events))
    def test_rejection_is_terminal_but_does_not_clear_unreads(self):
        self.receipt({'err':'rejected'});self.bridge.unreads['~nec']=3
        with self.assertRaisesRegex(ValueError,'rejected'):self.bridge.accept('~nec')
        self.bridge.mark_read('~nec');self.assertEqual(self.bridge.unreads['~nec'],3)
        self.assertEqual(self.bridge.effects.unresolved(),[])
        self.assertEqual({r['phase'] for r in json.loads(self.bridge.effects.path.read_text())['effects']},{'rejected'})
    def test_old_connection_receipt_cannot_change_new_connection_unreads(self):
        self.bridge.unreads['~nec']=7
        def send(path,raw,method):
            row=json.loads(raw)[0]
            self.bridge.on_fact({'id':row['id'],'response':'poke','ok':'ok'},None)
            self.bridge.generation+=1;self.bridge.ship='~bus'
            return b''
        self.bridge.request=send;self.bridge.mark_read('~nec')
        self.assertEqual(self.bridge.unreads['~nec'],7)
        self.assertFalse(any(n=='unreads' for n,_ in self.events))
        row=json.loads(self.bridge.effects.path.read_text())['effects'][0]
        self.assertEqual((row['ship'],row['phase']),('~zod','acknowledged'))

    def test_journal_failure_prevents_http_dispatch(self):
        self.receipt({'ok':'ok'})
        with patch.object(self.bridge.effects,'_write',side_effect=OSError('disk full')):
            with self.assertRaises(StoreError):self.bridge.accept('~nec')
            self.bridge.mark_read('~nec')
        self.assertEqual(self.requests,[]);self.assertEqual(self.bridge.pending,{})
    def test_unknown_format_and_permissions_are_preserved(self):
        j=self.bridge.effects;j.store._directory();raw=b'{"version":99,"effects":[]}'
        j.path.write_bytes(raw);j.path.chmod(0o600)
        with self.assertRaises(StoreError):j.begin(self.bridge.base,'~zod','~nec','mark-read')
        self.assertEqual(j.path.read_bytes(),raw)
        j.path.write_text('{"version":1,"effects":[]}');j.path.chmod(0o644)
        with self.assertRaises(StoreError):j.unresolved()
    def test_capacity_does_not_evict_unknown_effects(self):
        j=self.bridge.effects
        for _ in range(128):j.begin(self.bridge.base,'~zod','~nec','mark-read')
        before=j.path.read_bytes()
        with self.assertRaises(StoreError):j.begin(self.bridge.base,'~zod','~nec','accept-invitation')
        self.assertEqual(j.path.read_bytes(),before)
    def test_drain_retains_unknown_effect_references_without_dispatch(self):
        ident=self.bridge.effects.begin(self.bridge.base,'~zod','~nec','mark-read')
        before=self.bridge.effects.path.read_bytes();self.bridge.request=lambda *args:b''
        receipt=self.bridge.stop_transport()
        self.assertTrue(receipt['closed']);self.assertFalse(receipt['collectionReady'])
        self.assertEqual(receipt['unresolvedEffects'][0]['id'],ident)
        self.assertEqual(self.bridge.effects.path.read_bytes(),before)
    def test_unreadable_effects_prevent_false_drain_success(self):
        j=self.bridge.effects;j.store._directory();j.path.write_text('{"version":99}');j.path.chmod(0o600)
        self.bridge.request=lambda *args:b''
        self.assertFalse(self.bridge.stop_transport()['closed'])

if __name__=='__main__':unittest.main()
