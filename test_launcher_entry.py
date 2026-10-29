from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from bridge import Bridge
from launcher_entry import FILENAME, MARKER, desktop_arg, install_launcher, remove_launcher, launcher_path
from instance import Instance, CANONICAL_ID, STATE_NAME


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

    def test_profiles_keep_distinct_launchers_and_visible_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stable = Instance(CANONICAL_ID, "default", STATE_NAME, "Stable")
            dev = Instance(CANONICAL_ID + ".dev", "dev", STATE_NAME + ".dev", "Dev")
            edge = Instance(CANONICAL_ID + ".edge", "edge", STATE_NAME + ".edge", "Edge")
            for instance in (stable, dev, edge):
                script = root / instance.profile / "open_web.py"
                self.assertTrue(install_launcher("~zod", script, root, instance))
            stable_bytes = launcher_path(root, stable).read_bytes()
            self.assertIn("[dev]", launcher_path(root, dev).read_text())
            self.assertIn("[edge]", launcher_path(root, edge).read_text())
            remove_launcher(root, dev)
            self.assertFalse(launcher_path(root, dev).exists())
            self.assertTrue(launcher_path(root, edge).exists())
            self.assertEqual(launcher_path(root, stable).read_bytes(), stable_bytes)
            # Even a copied valid marker from another profile grants no ownership.
            wrong = launcher_path(root, dev)
            wrong.write_bytes(stable_bytes)
            self.assertFalse(install_launcher("~nec", root / "dev/open_web.py", root, dev))
            remove_launcher(root, dev)
            self.assertEqual(wrong.read_bytes(), stable_bytes)

    def test_legacy_launcher_is_owned_only_by_the_default_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dev = Instance(CANONICAL_ID + ".dev", "dev", STATE_NAME + ".dev", "Dev")
            legacy = "[Desktop Entry]\n" + MARKER + "\n"
            path = launcher_path(root, dev)
            path.parent.mkdir()
            path.write_text(legacy)
            self.assertFalse(install_launcher("~zod", root / "open_web.py", root, dev))
            remove_launcher(root, dev)
            self.assertEqual(path.read_text(), legacy)
            default = root / "applications" / FILENAME
            default.write_text(legacy)
            self.assertTrue(install_launcher("~zod", root / "open_web.py", root))

    def test_desktop_exec_arg_escapes_field_codes_and_quotes(self):
        self.assertEqual(desktop_arg('/some%path/"test".py'), '"/some%%path/\\"test\\".py"')
        with self.assertRaises(ValueError):
            desktop_arg('/tmp/a\nExec=wrong')

    def test_bridge_registers_saved_ship_and_removes_only_its_entry_on_signout(self):
        class Store:
            def account(self):
                return None

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
