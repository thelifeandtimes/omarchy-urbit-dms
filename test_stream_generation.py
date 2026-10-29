"""Stale stream callbacks must not act on a newly selected session."""
import unittest
import socket
from unittest.mock import patch
from bridge import Bridge

class EmptyStream:
    def __init__(self):self.client,self.peer=socket.socketpair()
    def __enter__(self):return self
    def __exit__(self,*args):self.client.close();self.peer.close()
    def fileno(self):return self.client.fileno()
    def __iter__(self):return iter(())

class Opener:
    def open(self,*args,**kwargs):return EmptyStream()

class StreamGenerationTests(unittest.TestCase):
    def setUp(self):
        self.bridge=Bridge(session_store=object())
        self.addCleanup(lambda:self.bridge.pool.shutdown(wait=True,cancel_futures=True))
        self.bridge.base='http://127.0.0.1:18081';self.bridge.ship='~zod';self.bridge.channel='old-channel'
        self.bridge.emit=lambda *_a,**_kw:None
        self.calls=[]
        self.bridge.request=lambda path,*a,**kw:self.calls.append(path) or b''

    def change_selection(self,*_args):
        self.bridge.generation+=1
        self.bridge.base='http://127.0.0.1:18082';self.bridge.ship='~nec';self.bridge.channel='new-channel'
        self.bridge.book={'new':'account'}

    def test_backoff_cannot_delete_new_session_channel(self):
        self.bridge.opener=Opener()
        with patch('bridge.time.sleep',self.change_selection):
            self.bridge.listen(self.bridge.generation)
        self.assertTrue(self.calls)
        self.assertNotIn('/~/channel/new-channel',self.calls)

    def test_queued_ack_cannot_follow_selection_or_reconnect(self):
        queued=[]
        self.bridge.pool.submit=lambda fn,*a,**kw:queued.append((fn,a,kw))
        self.bridge.on_fact({'response':'diff','json':{}},'19')
        self.assertEqual(len(queued),1)
        # Reconnect can change the channel without changing account generation.
        self.bridge.channel='new-channel'
        fn,args,kwargs=queued[0]
        try:fn(*args,**kwargs)
        except ValueError:pass
        self.assertEqual(self.calls,[])

    def test_old_contact_read_cannot_replace_new_account_book(self):
        def read(*_args):
            self.change_selection()
            return {'old':'account'}
        self.bridge.scry=read
        self.bridge.load_book()
        self.assertEqual(self.bridge.book,{'new':'account'})

if __name__=='__main__':unittest.main()
