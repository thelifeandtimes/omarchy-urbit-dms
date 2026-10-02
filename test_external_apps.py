import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from external_apps import focus_running_talon, launch_talon, talon_appimage


class TalonLaunchTests(unittest.TestCase):
    def test_desktop_entry_and_executable_appimage_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            app = home / "apps/Talon-x86_64.AppImage"
            app.parent.mkdir()
            app.write_bytes(b"test")
            self.assertIsNone(talon_appimage(home))
            app.chmod(0o700)
            self.assertEqual(talon_appimage(home), (app, False))
            entry = home / ".local/share/applications/Talon.desktop"
            entry.parent.mkdir(parents=True)
            entry.write_text("[Desktop Entry]\nExec=" + str(app) + "\n")
            self.assertEqual(talon_appimage(home), (app, True))

    def test_running_talon_is_focused_instead_of_launched_twice(self):
        commands = []

        def runner(args, **kwargs):
            commands.append(args)
            return subprocess.CompletedProcess(args, 0, json.dumps([
                {"class": "io-nisfeb-talon-compose-MainKt", "address": "0xabc"}
            ]) if args[1] == "clients" else "ok", "")

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            app = home / "apps/Talon-x86_64.AppImage"
            app.parent.mkdir()
            app.write_bytes(b"test")
            app.chmod(0o700)
            self.assertEqual(launch_talon(home, runner=runner,
                                          spawn=lambda *args, **kwargs: self.fail("duplicate launch")), "focused")
        self.assertEqual(commands[-1], ["hyprctl", "dispatch", 'hl.dsp.focus({window="address:0xabc"})'])

    def test_without_window_launches_appimage_directly(self):
        commands = []

        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, "[]", "")

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            app = home / "apps/Talon-x86_64.AppImage"
            app.parent.mkdir()
            app.write_bytes(b"test")
            app.chmod(0o700)
            self.assertEqual(launch_talon(home, runner=runner,
                                          spawn=lambda args, **kwargs: commands.append(args)), "launched")
            self.assertEqual(commands, [[str(app)]])

    def test_failed_focus_does_not_start_duplicate_instance(self):
        def runner(args, **kwargs):
            if args[1] == "clients":
                return subprocess.CompletedProcess(args, 0, json.dumps([
                    {"class": "Talon", "address": "0xabc"}
                ]), "")
            return subprocess.CompletedProcess(args, 1, "", "unable to focus")

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            app = home / "apps/Talon-x86_64.AppImage"
            app.parent.mkdir()
            app.write_bytes(b"test")
            app.chmod(0o700)
            with self.assertRaisesRegex(ValueError, "already running"):
                launch_talon(home, runner=runner,
                             spawn=lambda *args, **kwargs: self.fail("duplicate launch"))

    def test_failed_window_query_does_not_start_talon(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            app = home / "apps/Talon-x86_64.AppImage"
            app.parent.mkdir()
            app.write_bytes(b"test")
            app.chmod(0o700)
            with self.assertRaisesRegex(ValueError, "Could not check"):
                launch_talon(home,
                             runner=lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "error"),
                             spawn=lambda *args, **kwargs: self.fail("duplicate launch"))


if __name__ == "__main__":
    unittest.main()
