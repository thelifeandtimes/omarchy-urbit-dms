"""Explicit closure wakes an owned blocked reader without touching a replacement."""
import socket
import tempfile
import threading
import time
import unittest
from bridge import Bridge
from outbound_journal import OutboundJournal

class BlockingStream:
    def __init__(self):
        self.client,self.peer=socket.socketpair();self.reader=self.client.makefile('rb')
    def fileno(self):return self.client.fileno()
    def __enter__(self):return self
    def __exit__(self,*_):self.reader.close();self.client.close()
    def __iter__(self):return iter(self.reader)

class CancellationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.b=Bridge(session_store=object(),outbound_journal=OutboundJournal(tmp.name))
        self.addCleanup(self.b.release_transport_owner);self.addCleanup(self.b.pool.shutdown,wait=True)
        self.b.base='http://127.0.0.1:18081';self.b.ship='~zod';self.b.channel='old-channel';self.calls=[]
        self.b.request=lambda path,*a,**kw:self.calls.append(path) or b''
        self.b.emit=lambda *a,**kw:None
    def wait(self,predicate):
        end=time.monotonic()+2
        while time.monotonic()<end:
            if predicate():return
            time.sleep(.005)
        self.fail('stream state did not settle')
    def start(self,stream):
        self.addCleanup(stream.peer.close);self.b.opener.open=lambda *a,**kw:stream
        t=threading.Thread(target=self.b.listen,args=(self.b.generation,),daemon=True);self.b.listeners.append(t);t.start();return t
    def test_confirmed_delete_wakes_blocked_read_without_server_eof(self):
        stream=BlockingStream();t=self.start(stream);self.wait(lambda:len(self.b.stream_sockets)==1)
        result=self.b.stop_transport()
        self.assertTrue(result['closed']);self.assertFalse(t.is_alive());self.assertEqual(self.b.stream_sockets,{})
        self.assertGreaterEqual(stream.peer.fileno(),0)
        self.assertEqual(len(self.calls),5) # Four subscriptions, one deletion.
    def test_late_http_open_cannot_register_after_stop_or_target_new_channel(self):
        stream=BlockingStream();self.addCleanup(stream.peer.close)
        opened=threading.Event();release=threading.Event()
        def delayed(*a,**kw):opened.set();release.wait(2);return stream
        self.b.opener.open=delayed
        t=threading.Thread(target=self.b.listen,args=(0,),daemon=True);t.start();self.assertTrue(opened.wait(1))
        with self.b.channel_lock:self.b.generation=1;self.b.channel='new-channel'
        release.set();t.join(2)
        self.assertFalse(t.is_alive());self.assertEqual(self.b.stream_sockets,{})
        self.assertNotIn('/~/channel/new-channel',self.calls)
    def test_old_reader_exit_does_not_cancel_replacement_reader(self):
        first=BlockingStream();old=self.start(first);self.wait(lambda:len(self.b.stream_sockets)==1)
        self.b.reset_runtime();old.join(2);self.assertFalse(old.is_alive())
        self.b.base='http://127.0.0.1:18082';self.b.ship='~nec';self.b.channel='new-channel'
        second=BlockingStream();new=self.start(second);self.wait(lambda:len(self.b.stream_sockets)==1)
        self.b.cancel_streams((0,'old-channel'))
        self.assertTrue(new.is_alive());self.assertIn((1,'new-channel'),self.b.stream_sockets)
        self.assertTrue(self.b.stop_transport()['closed']);self.assertFalse(new.is_alive())

if __name__=='__main__':unittest.main()
