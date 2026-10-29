"""Only an exact positive Gall receipt can report a message/invitation completed."""
import json
import threading
import tempfile
from pathlib import Path
from outbound_journal import OutboundJournal
import unittest
from bridge import Bridge

class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.bridge=Bridge(session_store=object(),outbound_journal=OutboundJournal(Path(self.temp.name)))
        self.bridge.base="http://127.0.0.1:18081"
        self.addCleanup(lambda:self.bridge.pool.shutdown(wait=True,cancel_futures=True))
        self.bridge.ship='~zod';self.bridge.channel='fixture'
        self.events=[];self.requests=[]
        self.bridge.emit=lambda event,**fields:self.events.append((event,fields))
        self.bridge.refresh=lambda *a,**kw:None

    def receipt(self, fields):
        def request(path,data,method):
            body=json.loads(data)[0];self.requests.append(body)
            self.bridge.on_fact(dict(id=body['id'],response='poke',**fields),None)
            return b''
        self.bridge.request=request

    def test_missing_or_unrecognized_positive_receipt_is_uncertain(self):
        for fields in ({},{'ok':'unexpected'},{'ok':True},{'err':None}):
            for operation in ('send','accept'):
                with self.subTest(fields=fields,operation=operation):
                    self.events.clear();self.requests.clear();self.receipt(fields)
                    with self.assertRaisesRegex(ValueError,'status unknown'):
                        if operation=='send':self.bridge.send('~nec','one write','fixture-request')
                        else:self.bridge.accept('~nec')
                    self.assertEqual(len(self.requests),1)
                    self.assertFalse(any(e in ('sent','accepted') for e,_ in self.events))

    def test_boolean_or_float_identifier_cannot_match_integer_request(self):
        for ident in (True,1.0,'1'):
            with self.subTest(ident=ident):
                ack=threading.Event();self.bridge.pending[1]=(ack,{})
                self.bridge.on_fact({'id':ident,'response':'poke','ok':'ok'},None)
                self.assertFalse(ack.is_set())

    def test_first_receipt_cannot_be_overwritten_by_a_late_duplicate(self):
        ack=threading.Event();result={};self.bridge.pending[1]=(ack,result)
        self.bridge.on_fact({'id':1,'response':'poke','err':'rejected'},None)
        self.assertTrue(ack.is_set());before=dict(result)
        self.bridge.on_fact({'id':1,'response':'poke','ok':'ok'},None)
        self.assertEqual(result,before)

    def test_exact_positive_ack_and_explicit_rejection(self):
        self.receipt({'ok':'ok'});self.bridge.send('~nec','accepted','fixture-request')
        self.assertEqual(len([e for e,_ in self.events if e=='sent']),1)
        self.events.clear();self.requests.clear();self.receipt({'err':'rejected'})
        with self.assertRaisesRegex(ValueError,'rejected'):self.bridge.send('~nec','rejected','fixture-request')
        self.assertEqual(len(self.requests),1);self.assertFalse(self.events)

if __name__=='__main__':unittest.main()
