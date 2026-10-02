from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from bridge import Bridge
from launcher_entry import FILENAME, MARKER, desktop_arg, install_launcher, remove_launcher


class LauncherEntryTests(unittest.TestCase):
    def test_idempotent_launcher_without_credentials_or_web_url_in_exec(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'some path' / 'open_web.py'
            self.assertTrue(install_launcher('~sarlev', script, root))
            entry = root / 'applications' / FILENAME
            text = entry.read_text()
            self.assertIn('Name=Tlon Web (~sarlev)', text)
            self.assertIn('Exec=python3 "' + str(script) + '"', text)
            self.assertIn(MARKER, text)
            self.assertNotIn('urbauth-', text)
            self.assertNotIn('/apps/groups', text)
            self.assertTrue(install_launcher('~zod', script, root))
            self.assertIn('Name=Tlon Web (~zod)', entry.read_text())
            remove_launcher(root)
            self.assertFalse(entry.exists())

    def test_unrelated_desktop_entry_is_never_overwritten_or_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / 'applications' / FILENAME
            entry.parent.mkdir()
            entry.write_text('[Desktop Entry]\nName=My app\n')
            self.assertFalse(install_launcher('~sarlev', root / 'open_web.py', root))
            remove_launcher(root)
            self.assertEqual(entry.read_text(), '[Desktop Entry]\nName=My app\n')

    def test_desktop_exec_arg_escapes_field_codes_and_quotes(self):
        self.assertEqual(desktop_arg('/some%path/"test".py'), '"/some%%path/\\"test\\".py"')
        with self.assertRaises(ValueError):
            desktop_arg('/tmp/a\nExec=wrong')

    def test_bridge_registers_saved_ship_and_removes_only_its_entry_on_signout(self):
        class Store:
            def forget(self):
                pass

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_DATA_HOME": directory}):
            bridge = Bridge(Store())
            bridge.manage_launcher = True
            bridge.emit = lambda *args, **kwargs: None
            bridge.ship = "~zod"
            bridge.sync_launcher()
            entry = Path(directory) / "applications" / FILENAME
            self.assertIn("Tlon Web (~zod)", entry.read_text())
            bridge.logout()
            self.assertFalse(entry.exists())


if __name__ == '__main__':
    unittest.main()
