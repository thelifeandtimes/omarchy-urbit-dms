"""Transport evidence survives owner loss without replaying application work."""
import copy
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from instance import Instance
import transport_journal as t
from session_store import StoreError

SESSION={'url':'http://127.0.0.1:18081','ship':'~zod','cookieName':'urbauth-~zod','cookieValue':'synthetic-private-cookie'}


class TransportJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='omabit-channel-journal-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'account';self.j=t.TransportJournal(self.root)
    def bytes(self):return self.j.path.read_bytes()
    def row(self):return self.j.status()['channels'][0]
    def test_observation_never_initializes_state(self):
        self.assertIsNone(self.j.status());self.assertFalse(self.root.exists())
    def test_record_before_network_and_exact_cleanup_replay(self):
        with self.j.owner() as lease:
            row=self.j.begin(lease,SESSION);self.assertEqual(row,self.row());self.assertEqual(row['phase'],'open')
            self.assertNotIn(SESSION['cookieValue'].encode(),self.bytes())
            self.assertEqual(self.j.path.stat().st_mode&0o777,0o600)
            closing=self.j.prepare_cleanup(lease,row['id'],SESSION);before=self.bytes()
            self.assertEqual(closing,self.j.prepare_cleanup(lease,row['id'],SESSION));self.assertEqual(self.bytes(),before)
            closed=self.j.deleted(lease,row['id'],closing['cleanup_request'],204);before=self.bytes()
            self.assertEqual(closed,self.j.deleted(lease,row['id'],closing['cleanup_request'],204))
            self.assertEqual(closed,self.j.prepare_cleanup(lease,row['id'],SESSION));self.assertEqual(self.bytes(),before)
            second=self.j.begin(lease,SESSION);self.assertNotEqual(row['channel'],second['channel'])
    def test_pending_channel_blocks_replacement_and_is_never_discarded(self):
        with self.j.owner() as lease:
            row=self.j.begin(lease,SESSION);before=self.bytes()
            with self.assertRaises(StoreError):self.j.begin(lease,SESSION)
            self.assertEqual(self.bytes(),before);self.j.prepare_cleanup(lease,row['id'],SESSION);before=self.bytes()
            with self.assertRaises(StoreError):self.j.begin(lease,SESSION)
            self.assertEqual(self.bytes(),before)
    def test_cleanup_requires_exact_origin_ship_and_session(self):
        with self.j.owner() as lease:
            row=self.j.begin(lease,SESSION);before=self.bytes()
            for changes in ({'url':'http://127.0.0.1:18082'},{'ship':'~nec','cookieName':'urbauth-~nec'},{'cookieValue':'another-session'}):
                with self.subTest(changes=changes),self.assertRaises(StoreError):self.j.prepare_cleanup(lease,row['id'],{**SESSION,**changes})
                self.assertEqual(self.bytes(),before)
    def test_response_requires_prepared_identity_and_valid_status(self):
        with self.j.owner() as lease:
            row=self.j.begin(lease,SESSION)
            with self.assertRaises(StoreError):self.j.deleted(lease,row['id'],'a'*32,204)
            closing=self.j.prepare_cleanup(lease,row['id'],SESSION);before=self.bytes()
            for request,status in [('b'*32,204),(closing['cleanup_request'],True),(closing['cleanup_request'],401),(closing['cleanup_request'],500)]:
                with self.assertRaises(StoreError):self.j.deleted(lease,row['id'],request,status)
                self.assertEqual(self.bytes(),before)
    def test_closed_lease_cannot_dispatch_or_record(self):
        with self.j.owner() as lease:row=self.j.begin(lease,SESSION)
        before=self.bytes()
        with self.assertRaises(StoreError):self.j.prepare_cleanup(lease,row['id'],SESSION)
        self.assertEqual(self.bytes(),before)
    def test_concurrent_owner_refuses_and_alive_prior_owner_is_not_recoverable(self):
        with self.j.owner() as lease:
            row=self.j.begin(lease,SESSION)
            with self.assertRaises(BlockingIOError):
                with t.TransportJournal(self.root).owner():pass
        # Merely releasing a lock while this process lives proves no cleanup.
        with self.j.owner() as lease:
            before=self.bytes()
            with self.assertRaises(StoreError):self.j.prepare_cleanup(lease,row['id'],SESSION)
            self.assertEqual(self.bytes(),before)
    def test_other_pid_namespace_or_unreadable_process_is_unknown(self):
        owner={'id':'a'*32,**t.process_identity()}
        different={**owner,'namespace':'pid:[1]'};self.assertEqual(t.owner_status(different),'unknown')
        with patch.object(t.Path,'read_text',side_effect=PermissionError()):self.assertEqual(t.owner_status(owner),'unknown')
        with patch.object(t,'process_identity',return_value={**t.process_identity(),'boot':'00000000-0000-0000-0000-000000000000'}):self.assertEqual(t.owner_status(owner),'dead')
    def test_owner_pid_start_ticks_prevent_reuse(self):
        owner={'id':'a'*32,**t.process_identity()};self.assertEqual(t.owner_status(owner),'alive')
        owner['start']=str(int(owner['start'])+1);self.assertEqual(t.owner_status(owner),'dead')
    def test_unknown_exit_never_allows_cleanup(self):
        with self.j.owner() as lease:row=self.j.begin(lease,SESSION)
        with self.j.owner() as lease,patch.object(t,'owner_status',return_value='unknown'):
            before=self.bytes()
            with self.assertRaises(StoreError):self.j.prepare_cleanup(lease,row['id'],SESSION)
            self.assertEqual(self.bytes(),before)
    def test_live_cleanup_actor_refuses_takeover(self):
        with self.j.owner() as lease:row=self.j.begin(lease,SESSION);self.j.prepare_cleanup(lease,row['id'],SESSION)
        with self.j.owner() as lease,patch.object(t,'owner_status',side_effect=['dead','alive']):
            before=self.bytes()
            with self.assertRaises(StoreError):self.j.prepare_cleanup(lease,row['id'],SESSION)
            self.assertEqual(self.bytes(),before)
    def child(self,closing=False):
        code='''import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import transport_journal as t
j=t.TransportJournal(sys.argv[2])
with j.owner() as lease:
 row=j.begin(lease,json.loads(sys.argv[3]))
 if sys.argv[4]=='closing':row=j.prepare_cleanup(lease,row['id'],json.loads(sys.argv[3]))
 print(json.dumps(row),flush=True);sys.stdin.read()
'''
        proc=subprocess.Popen([sys.executable,'-B','-c',code,str(Path(t.__file__).parent),str(self.root),json.dumps(SESSION),'closing' if closing else 'open'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def cleanup():
            if proc.poll() is None:proc.kill();proc.wait(timeout=5)
            for stream in (proc.stdin,proc.stdout,proc.stderr):stream.close()
        self.addCleanup(cleanup);self.assertTrue(select.select([proc.stdout],[],[],5)[0]);line=proc.stdout.readline()
        self.assertTrue(line);return proc,json.loads(line)
    def test_actual_owner_kill_releases_lock_and_retains_exact_channel(self):
        proc,row=self.child()
        with self.assertRaises(BlockingIOError):
            with self.j.owner():pass
        proc.kill();self.assertEqual(proc.wait(timeout=5),-signal.SIGKILL)
        with self.j.owner() as lease:
            closing=self.j.prepare_cleanup(lease,row['id'],SESSION);self.assertEqual(closing['channel'],row['channel'])
            self.assertEqual(closing['owner'],row['owner']);self.assertNotEqual(closing['cleanup_actor'],row['owner'])
            self.assertEqual(self.j.deleted(lease,row['id'],closing['cleanup_request'],204)['phase'],'closed')
    def test_actual_cleanup_owner_kill_preserves_request_for_retry(self):
        proc,row=self.child(closing=True);proc.kill();proc.wait(timeout=5)
        with self.j.owner() as lease:
            closing=self.j.prepare_cleanup(lease,row['id'],SESSION)
            self.assertEqual(closing['cleanup_request'],row['cleanup_request']);self.assertNotEqual(closing['cleanup_actor'],row['cleanup_actor'])
            self.assertEqual(self.j.deleted(lease,row['id'],closing['cleanup_request'],204)['phase'],'closed')
    def test_missing_existing_journal_never_reinitializes(self):
        with self.j.owner():pass
        self.j.path.rename(self.j.path.with_suffix('.retained'))
        with self.assertRaises(StoreError):
            with self.j.owner():pass
        self.assertFalse(self.j.path.exists())
    def test_unknown_formats_duplicate_keys_and_sensitive_fields_refuse_intact(self):
        with self.j.owner() as lease:self.j.begin(lease,SESSION)
        original=self.bytes();value=json.loads(original)
        bad=[]
        for version in (2,True):
            v=copy.deepcopy(value);v['version']=version;bad.append(json.dumps(v).encode())
        v=copy.deepcopy(value);v['channels'][0]['cookieValue']='must-never-be-accepted';bad.append(json.dumps(v).encode())
        bad.append(original.replace(b'"version":1',b'"version":1,"version":1'))
        for raw in bad:
            self.j.path.write_bytes(raw)
            with self.assertRaises(StoreError):self.j.status()
            self.assertEqual(self.bytes(),raw)
    def test_another_plugin_instance_cannot_open_history(self):
        with self.j.owner():pass
        before=self.bytes();other=Instance('urbit.dms.other','other','omarchy-urbit-dms.other','Other')
        with self.assertRaises(StoreError):t.TransportJournal(self.root,other).status()
        self.assertEqual(self.bytes(),before)
    def test_unsafe_journal_objects_refuse_intact(self):
        with self.j.owner():pass
        original=self.bytes();self.j.path.chmod(0o644)
        with self.assertRaises(StoreError):self.j.status()
        self.assertEqual(self.bytes(),original);self.j.path.chmod(0o600)
        link=self.j.root/'retained';os.link(self.j.path,link)
        with self.assertRaises(StoreError):self.j.status()
        self.assertEqual(link.read_bytes(),original);self.j.path.unlink();self.j.path.symlink_to(link)
        with self.assertRaises((StoreError,OSError)):self.j.status()
        self.assertEqual(link.read_bytes(),original)
    def test_failure_before_replace_retains_old_state_and_temporary(self):
        with self.j.owner() as lease:
            row=self.j.begin(lease,SESSION);before=self.bytes()
            with patch.object(t.os,'replace',side_effect=OSError('injected before rename')):
                with self.assertRaises(OSError):self.j.prepare_cleanup(lease,row['id'],SESSION)
            self.assertEqual(self.bytes(),before);self.assertEqual(len(list(self.j.root.glob('.pending-*'))),1)
            self.assertEqual(self.j.prepare_cleanup(lease,row['id'],SESSION)['phase'],'closing')
    def test_lost_directory_sync_ack_reuses_cleanup_request(self):
        with self.j.owner() as lease:
            row=self.j.begin(lease,SESSION)
            with patch.object(self.j,'_sync',side_effect=OSError('injected after rename')):
                with self.assertRaises(OSError):self.j.prepare_cleanup(lease,row['id'],SESSION)
            saved=self.row();before=self.bytes();retry=self.j.prepare_cleanup(lease,row['id'],SESSION)
            self.assertEqual(saved,retry);self.assertEqual(self.bytes(),before)
    def test_receipt_limit_preserves_all_history(self):
        with self.j.owner() as lease:
            row=self.j.begin(lease,SESSION);closing=self.j.prepare_cleanup(lease,row['id'],SESSION);self.j.deleted(lease,row['id'],closing['cleanup_request'],204)
            before=self.bytes()
            with patch.object(t,'MAX_CHANNELS',1):
                with self.assertRaises(StoreError):self.j.begin(lease,SESSION)
            self.assertEqual(self.bytes(),before)

if __name__=='__main__':unittest.main(verbosity=2)
