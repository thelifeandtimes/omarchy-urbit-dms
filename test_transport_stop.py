"""Transport closure must retain uncertain work and refuse false cleanup success."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from bridge import Bridge
from outbound_journal import OutboundJournal

class TransportStopTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.journal=OutboundJournal(self.temp.name)
        self.bridge=Bridge(session_store=object(),outbound_journal=self.journal)
        self.addCleanup(lambda:self.bridge.pool.shutdown(wait=True,cancel_futures=True))
        self.bridge.base='http://127.0.0.1:18081';self.bridge.ship='~zod';self.bridge.channel='owned-channel'
        self.events=[];self.calls=[]
        self.bridge.emit=lambda name,**data:self.events.append((name,data))
        self.bridge.request=lambda path,data,method:self.calls.append((path,json.loads(data))) or b''

    def test_deletes_owned_channel_preserves_session_and_unknown_send(self):
        ident=self.journal.begin(self.bridge.base,'~zod','~nec',{'id':'~zod/123','author':'~zod','sent':1,'text':'unknown'})
        digest=self.journal.path.read_bytes()
        result=self.bridge.stop_transport()
        self.assertTrue(result['closed']);self.assertFalse(result['collectionReady'])
        self.assertEqual(result['unresolvedSends'][0]['id'],ident)
        self.assertEqual(self.calls[0][0],'/~/channel/owned-channel')
        self.assertEqual(self.calls[0][1][0]['action'],'delete')
        self.assertEqual(self.bridge.channel,'');self.assertEqual(self.bridge.ship,'~zod')
        self.assertEqual(self.journal.path.read_bytes(),digest)
        self.bridge.command({'action':'send','ship':'~nec','text':'not admitted','request':5})
        self.assertEqual(len(self.calls),1)
        self.assertTrue(any(e=='error' and x.get('request')==5 for e,x in self.events))

    def test_delete_failure_is_blocked_and_retry_keeps_exact_binding(self):
        def fail(*args):raise OSError('reply unavailable')
        self.bridge.request=fail;generation=self.bridge.generation
        result=self.bridge.stop_transport()
        self.assertFalse(result['closed']);self.assertEqual(result['reason'],'channel-cleanup-unknown')
        self.assertEqual(self.bridge.channel,'owned-channel');self.assertEqual(self.bridge.generation,generation)
        self.bridge.request=lambda path,data,method:self.calls.append(path) or b''
        self.assertTrue(self.bridge.stop_transport()['closed'])
        self.assertEqual(self.calls,['/~/channel/owned-channel'])

    def test_pending_receipt_prevents_channel_deletion(self):
        self.bridge.pending[9]=(threading.Event(),{})
        self.assertEqual(self.bridge.stop_transport(),{'closed':False,'reason':'pending-receipts'})
        self.assertEqual(self.calls,[])

    def test_existing_worker_finishes_before_channel_deletion(self):
        finished=threading.Event()
        self.bridge.pool.submit(finished.set)
        original=self.bridge.request
        def request(*args):
            self.assertTrue(finished.is_set());return original(*args)
        self.bridge.request=request
        self.assertTrue(self.bridge.stop_transport()['closed'])

    def test_listener_exit_is_required(self):
        class Listener:
            def join(self,timeout):pass
            def is_alive(self):return True
        self.bridge.listeners=[Listener()]
        result=self.bridge.stop_transport()
        self.assertFalse(result['closed']);self.assertEqual(result['reason'],'stream-still-open')
        self.assertEqual(len(self.calls),1)
        self.bridge.listeners=[]
        self.assertTrue(self.bridge.stop_transport()['closed'])
        self.assertEqual(len(self.calls),1)

    def test_unreadable_outbound_state_cannot_report_closed(self):
        self.journal.store._directory();self.journal.path.write_text('{"version":99}');self.journal.path.chmod(0o600)
        result=self.bridge.stop_transport()
        self.assertFalse(result['closed']);self.assertEqual(result['reason'],'outbound-history-unavailable')

if __name__=='__main__':unittest.main()
