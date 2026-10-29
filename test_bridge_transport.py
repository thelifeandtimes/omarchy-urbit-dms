"""Bridge recovery preserves exact channel ownership and uncertain application work."""
import io
import urllib.error
import tempfile
import unittest
from unittest.mock import patch
from bridge import Bridge
from outbound_journal import OutboundJournal
from session_store import StoreError,cookie_for
from test_bridge import MemoryStore
from test_transport_journal import SESSION


class Response(io.BytesIO):
    def __init__(self,status=204,body=b''):
        super().__init__(body);self.status=status


class BridgeTransportTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=MemoryStore();self.store.save(dict(SESSION))
        self.b=Bridge(session_store=self.store,outbound_journal=OutboundJournal(temp.name))
        self.addCleanup(self.b.release_transport_owner)
        self.addCleanup(self.b.pool.shutdown,wait=True,cancel_futures=True)
        self.b.base=SESSION['url'];self.b.ship=SESSION['ship'];self.b.transport_session=dict(SESSION)
        self.b.cookies.set_cookie(cookie_for(SESSION));self.calls=[]
        self.b.opener.open=self.response
        self.b.new_channel()
    def row(self):return self.b.transport.status()['channels'][-1]
    def response(self,request,timeout):
        self.calls.append((request.full_url,request.data,self.row()))
        return Response()
    def writes_refuse(self):
        for action in (lambda:self.b.channel_action({'action':'ack','event-id':1}),lambda:self.b.send('~nec','paused',1),lambda:self.b.acknowledged_effect('~nec','accept-invitation')):
            with self.assertRaises(StoreError):action()
    def test_record_precedes_first_network_and_delete_receipt_is_exact(self):
        self.assertEqual(self.calls,[]);row=self.row()
        self.b.channel_action({'action':'subscribe','ship':'zod','app':'chat','path':'/v4'})
        self.assertEqual(self.calls[0][2],row)
        self.b.close_channel()
        self.assertEqual(self.calls[1][0],SESSION['url']+'/~/channel/'+row['channel'])
        self.assertEqual(self.calls[1][1],b'[{"action":"delete"}]')
        self.assertEqual(self.calls[1][2]['phase'],'closing');self.assertEqual(self.row()['phase'],'closed')
    def test_revoked_receiver_clears_visible_account_and_records_denial(self):
        self.store.session=None
        def denied(request,timeout):
            raise urllib.error.HTTPError(request.full_url,403,"revoked",{},None)
        self.b.opener.open=denied
        events=[]
        self.b.emit=lambda event,**data:events.append((event,data))
        self.b.check_account()
        self.assertEqual(self.b.ship,"");self.assertEqual(self.b.base,"")
        self.assertEqual(self.row()["phase"],"closed")
        self.assertEqual(self.row()["delete_status"],403)
        self.assertIn(("logout",{"revoked":True}),events)
        self.assertEqual(self.b.outbound.unresolved(),[])

    def test_quick_reconnect_same_ship_does_not_keep_old_session(self):
        self.b.account_request_id="a"*32
        self.store.account=lambda:{"url":SESSION["url"],"ship":SESSION["ship"],"application_request_id":"b"*32}
        events=[];self.b.emit=lambda event,**data:events.append((event,data))
        self.b.check_account()
        self.assertEqual(self.b.ship,"")
        self.assertEqual(self.row()["phase"],"closed")
        self.assertIn(("logout",{"revoked":True}),events)

    def test_lost_delete_reply_blocks_writes_account_change_and_new_channel(self):
        old=self.row()
        def lost(*args,**kwargs):raise OSError('reply lost')
        self.b.opener.open=lost
        with self.assertRaises(OSError):self.b.close_channel()
        closing=self.row();self.writes_refuse()
        with self.assertRaises(OSError):self.b.login('https://other.example','another-code')
        self.assertEqual(self.store.session,SESSION)
        with self.assertRaises(OSError):self.b.new_channel()
        self.assertEqual(self.row(),closing);self.assertEqual(self.b.channel,old['channel'])
        self.b.opener.open=self.response;self.b.close_channel()
        closed=self.row();self.assertEqual(closed['cleanup_request'],closing['cleanup_request'])
        self.b.new_channel();self.assertNotEqual(self.b.channel,old['channel'])
        self.assertEqual(len(self.calls),1)
    def test_cleanup_prepare_failure_also_blocks_new_writes(self):
        before=self.row()
        with patch.object(self.b.transport,'prepare_cleanup',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.b.close_channel()
        self.assertEqual(self.row(),before);self.assertEqual(self.calls,[]);self.writes_refuse()
        self.b.close_channel();self.assertEqual(self.row()['phase'],'closed')
    def test_deleted_fsync_failure_retries_same_saved_request_without_new_channel(self):
        original=self.b.transport._sync
        count=0
        def sync():
            nonlocal count
            count+=1;original()
            if count==2:raise OSError('delete receipt acknowledgement lost')
        with patch.object(self.b.transport,'_sync',side_effect=sync):
            with self.assertRaises(OSError):self.b.close_channel()
        self.assertEqual(self.row()['phase'],'closed');self.writes_refuse()
        self.b.close_channel();self.assertEqual(len(self.calls),1);self.assertEqual(self.b.channel,'')
    def test_cleanup_target_is_the_saved_origin_not_mutable_display_state(self):
        row=self.row();self.b.base='https://wrong.example'
        self.b.close_channel()
        self.assertEqual(self.calls[0][0],SESSION['url']+'/~/channel/'+row['channel'])
        self.assertEqual(self.row()['phase'],'closed')
    def test_wrong_session_refuses_cleanup_without_http(self):
        before=self.row();self.b.transport_session={**SESSION,'cookieValue':'changed'}
        with self.assertRaises(StoreError):self.b.close_channel()
        self.assertEqual(self.row(),before);self.assertEqual(self.calls,[]);self.writes_refuse()
    def test_nonempty_or_unsuccessful_delete_response_preserves_pending(self):
        request=None
        for status,body in ((200,b'{"ok":true}'),(202,b''),(403,b'')):
            self.b.opener.open=lambda *a,**kw:Response(status,body)
            with self.assertRaises(StoreError):self.b.close_channel()
            row=self.row();self.assertEqual(row['phase'],'closing');self.writes_refuse()
            if request:self.assertEqual(row['cleanup_request'],request)
            request=row['cleanup_request']
        self.b.opener.open=self.response;self.b.close_channel()
        self.assertEqual(self.row()['cleanup_request'],request)
    def test_drain_keeps_unknown_app_receipts_and_credential(self):
        self.b.outbound.begin(SESSION['url'],'~zod','~nec',{'id':'~zod/123','author':'~zod','sent':1,'text':'uncertain'})
        self.b.effects.begin(SESSION['url'],'~zod','~nec','mark-read')
        before=(self.b.outbound.path.read_bytes(),self.b.effects.path.read_bytes())
        result=self.b.stop_transport()
        self.assertTrue(result['closed']);self.assertFalse(result['collectionReady'])
        self.assertEqual(before,(self.b.outbound.path.read_bytes(),self.b.effects.path.read_bytes()))
        self.assertEqual(self.store.session,SESSION);self.assertEqual(len(self.calls),1)
    def test_failed_start_does_not_leave_ship_appearing_connected(self):
        self.b.close_channel()
        with patch.object(self.b.transport,'begin',side_effect=OSError('begin not saved')):
            with self.assertRaises(OSError):self.b.start_session()
        self.assertEqual(self.b.ship,'');self.assertEqual(self.b.channel,'')
        self.assertEqual(self.store.session,SESSION);self.assertEqual(self.b.listeners,[])

if __name__=='__main__':unittest.main()
