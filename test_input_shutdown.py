"""The bridge drains even when its shell closes stdin or stdout unexpectedly."""
import io
import json
import unittest
from unittest.mock import Mock,patch
import bridge


class InputShutdownTests(unittest.TestCase):
    def helper(self,results):
        value=Mock();value.closing=False;value.stop_transport.side_effect=results
        return value
    def run_main(self,value,text=''):
        with patch.object(bridge,'Bridge',return_value=value),patch.object(bridge,'talon_appimage',return_value=None),patch.object(bridge.sys,'stdin',io.StringIO(text)):
            return bridge.main()
    def test_eof_drains_without_emitting_a_receipt_to_a_missing_shell(self):
        value=self.helper([{'closed':True}]);self.assertEqual(self.run_main(value),0)
        value.stop_transport.assert_called_once_with()
        self.assertFalse(any(call.args[0]=='transport-state' for call in value.emit.call_args_list))
    def test_explicit_closed_receipt_does_not_drain_twice(self):
        value=self.helper([{'closed':True}]);self.assertEqual(self.run_main(value,'{"action":"shutdown-transport"}\n'),0)
        value.stop_transport.assert_called_once_with();value.emit.assert_any_call('transport-state',closed=True)
    def test_failed_explicit_close_gets_one_eof_retry(self):
        value=self.helper([{'closed':False,'reason':'channel-cleanup-unknown'},{'closed':True}])
        self.assertEqual(self.run_main(value,'{"action":"shutdown-transport"}\n'),0)
        self.assertEqual(value.stop_transport.call_count,2)
    def test_unconfirmed_eof_cleanup_is_a_nonzero_exit(self):
        value=self.helper([{'closed':False,'reason':'channel-cleanup-unknown'}]);error=io.StringIO()
        with patch.object(bridge.sys,'stderr',error):self.assertEqual(self.run_main(value),1)
        self.assertEqual(error.getvalue(),'DMs connection cleanup remains unconfirmed\n')
    def test_broken_output_pipe_still_drains(self):
        value=self.helper([{'closed':True}]);value.emit.side_effect=BrokenPipeError('private pipe closed')
        with self.assertRaises(BrokenPipeError):self.run_main(value)
        value.stop_transport.assert_called_once_with()
    def test_invalid_input_does_not_skip_final_drain(self):
        value=self.helper([{'closed':True}]);self.assertEqual(self.run_main(value,'invalid json\n'),0)
        value.stop_transport.assert_called_once_with();value.emit.assert_any_call('error',message='Invalid command')

if __name__=='__main__':unittest.main()
