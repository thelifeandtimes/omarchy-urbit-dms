"""Only a fresh, correctly bound positive state observation resolves unknown effects."""
import json
from pathlib import Path
import tempfile
import unittest
from bridge import Bridge
from effects_journal import EffectsJournal
from outbound_journal import OutboundJournal
from session_store import StoreError

URL='http://127.0.0.1:18081'
READ={'ship/~nec':{'count':4,'notify-count':1,'unread':None}}  # shallow action, child unreads remain

class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.journal=EffectsJournal(self.temp.name)
        self.invite=self.journal.begin(URL,'~zod','~nec','accept-invitation')
        self.read=self.journal.begin(URL,'~zod','~nec','mark-read')
    def test_positive_observation_resolves_desired_state_without_claiming_ack(self):
        cohort=self.journal.candidates(URL,'~zod')
        self.assertTrue(self.journal.observe(URL,'~zod',cohort,['~nec'],READ))
        self.assertEqual(EffectsJournal(self.temp.name).unresolved(),[])
        rows=json.loads(self.journal.path.read_text())['effects']
        self.assertEqual({r['phase'] for r in rows},{'state-confirmed'})
    def test_missing_malformed_or_other_peer_data_does_not_resolve(self):
        cohort=self.journal.candidates(URL,'~zod');before=self.journal.path.read_bytes()
        for summary in ({},{'count':0},{'unread':None},{'unread':{},'count':0,'notify-count':0},
                        {'unread':{'count':0},'count':0,'notify-count':0},
                        {'unread':None,'count':False,'notify-count':0},
                        {'unread':None,'count':0,'notify-count':1},
                        {'unread':None,'count':-1,'notify-count':0}):
            with self.subTest(summary=summary):
                self.assertFalse(self.journal.observe(URL,'~zod',cohort,['~bus'],{'ship/~nec':summary}))
        self.assertEqual(self.journal.path.read_bytes(),before)
    def test_wrong_origin_ship_or_peer_is_not_evidence(self):
        cohort=self.journal.candidates(URL,'~zod')
        for url,ship in ((URL+'2','~zod'),(URL,'~bus')):
            self.assertFalse(self.journal.observe(url,ship,cohort,['~nec'],READ))
        self.assertFalse(self.journal.observe(URL,'~zod',cohort,['~bus'],{'ship/~bus':READ['ship/~nec']}))
        self.assertEqual(len(self.journal.unresolved()),2)
    def test_intent_created_after_read_started_is_not_resolved(self):
        cohort=self.journal.candidates(URL,'~zod')
        later=self.journal.begin(URL,'~zod','~nec','mark-read')
        self.journal.observe(URL,'~zod',cohort,['~nec'],READ)
        self.assertEqual([r['id'] for r in self.journal.unresolved()],[later])
    def test_late_exact_rejection_replaces_state_confirmation(self):
        self.journal.observe(URL,'~zod',self.journal.candidates(URL,'~zod'),['~nec'],READ)
        self.journal.finish(self.invite,'rejected')
        row=next(r for r in json.loads(self.journal.path.read_text())['effects'] if r['id']==self.invite)
        self.assertEqual(row['phase'],'rejected')
    def test_version_one_is_preserved_on_read_and_migrated_on_resolution(self):
        value=json.loads(self.journal.path.read_text());value['version']=1
        self.journal.path.write_text(json.dumps(value));before=self.journal.path.read_bytes()
        cohort=self.journal.candidates(URL,'~zod');self.assertEqual(self.journal.path.read_bytes(),before)
        self.journal.observe(URL,'~zod',cohort,['~nec'],READ)
        migrated=json.loads(self.journal.path.read_text());self.assertEqual(migrated['version'],2)
        self.assertEqual([r['id'] for r in migrated['effects']],[self.invite,self.read])
    def test_future_format_is_never_rewritten(self):
        value=json.loads(self.journal.path.read_text());value['version']=3
        self.journal.path.write_text(json.dumps(value));before=self.journal.path.read_bytes()
        with self.assertRaises(StoreError):self.journal.observe(URL,'~zod',[self.invite,self.read],['~nec'],READ)
        self.assertEqual(self.journal.path.read_bytes(),before)
    def bridge(self):
        bridge=Bridge(session_store=object(),outbound_journal=OutboundJournal(self.temp.name))
        self.addCleanup(lambda:bridge.pool.shutdown(wait=True,cancel_futures=True))
        bridge.base=URL;bridge.ship='~zod';bridge.channel='fixture'
        bridge.emit=lambda *_a,**_kw:None
        bridge.request=lambda *_a,**_kw:self.fail('reconciliation must never dispatch a write')
        return bridge
    def test_changed_connection_during_scry_cannot_resolve_or_change_unreads(self):
        bridge=self.bridge();bridge.unreads={'~nec':7}
        def scry(*_):bridge.generation+=1;bridge.ship='~bus';return READ
        bridge.scry=scry;bridge.refresh_unreads()
        self.assertEqual(len(self.journal.unresolved()),2);self.assertEqual(bridge.unreads,{'~nec':7})
    def test_same_connection_read_reconciles_only_read_intent(self):
        bridge=self.bridge();bridge.scry=lambda *_:READ;bridge.refresh_unreads()
        self.assertEqual([r['id'] for r in self.journal.unresolved()],[self.invite])
    def test_concurrent_new_intent_is_excluded_by_bridge_read_cohort(self):
        bridge=self.bridge();later=[]
        def scry(*_):later.append(self.journal.begin(URL,'~zod','~nec','mark-read'));return READ
        bridge.scry=scry;bridge.refresh_unreads()
        self.assertEqual({r['id'] for r in self.journal.unresolved()},{self.invite,*later})
    def test_unreadable_journal_does_not_block_authenticated_read_display(self):
        bridge=self.bridge();events=[];bridge.emit=lambda name,**data:events.append((name,data))
        self.journal.path.write_text('{"version":99}')
        bridge.scry=lambda *_:READ;bridge.refresh_unreads()
        self.assertTrue(any(n=='unreads' for n,_ in events))
        self.assertEqual(events[-1],('outbound-state',{'unresolved':[],'unresolvedEffects':[],'readable':False}))

if __name__=='__main__':unittest.main()
