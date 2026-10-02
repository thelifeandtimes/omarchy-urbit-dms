"""Optional Talon AppImage discovery and launch; no Talon dependency."""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess


DESKTOP = Path.home() / ".local/share/applications/Talon.desktop"
WINDOW_CLASS = "io-nisfeb-talon-compose-"


def talon_appimage(home=None):
    home = Path(home) if home is not None else Path.home()
    entry = home / ".local/share/applications/Talon.desktop"
    if entry.is_file():
        try:
            exec_line = next((line[5:].strip() for line in entry.read_text().splitlines()
                              if line.startswith("Exec=")), "")
            command = shlex.split(exec_line)
            path = Path(command[0]) if command else None
            if path and path.is_absolute() and path.name.lower().endswith(".appimage") and path.is_file() and os.access(path, os.X_OK):
                return path, True
        except (OSError, ValueError):
            pass
    for folder in (home / "apps", home / "Applications"):
        for path in sorted(folder.glob("Talon*.AppImage")):
            if path.is_file() and os.access(path, os.X_OK):
                return path, False
    return None


def focus_running_talon(runner=subprocess.run):
    try:
        result = runner(["hyprctl", "clients", "-j"], capture_output=True, text=True,
                        timeout=4, check=False)
        if result.returncode != 0:
            raise ValueError("Could not check for a running Talon window")
        windows = json.loads(result.stdout)
        if not isinstance(windows, list):
            raise ValueError("Could not check for a running Talon window")
        for window in windows:
            if not isinstance(window, dict):
                raise ValueError("Could not check for a running Talon window")
            window_class = str(window.get("class", ""))
            if not (window_class.startswith(WINDOW_CLASS) or window_class == "Talon"):
                continue
            address = window.get("address", "")
            if not isinstance(address, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", address):
                raise ValueError("Talon is already running but its window cannot be focused")
            return runner(["hyprctl", "dispatch", 'hl.dsp.focus({window="address:' + address + '"})'],
                          capture_output=True, timeout=4, check=False).returncode == 0
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        raise ValueError("Could not check for a running Talon window") from None
    return None


def launch_talon(home=None, runner=subprocess.run, spawn=subprocess.Popen):
    installed = talon_appimage(home)
    if not installed:
        raise ValueError("Install the Talon AppImage to open it from Urbit DMs")
    focused = focus_running_talon(runner)
    if focused is True:
        return "focused"
    if focused is False:
        raise ValueError("Talon is already running but could not be focused")
    path, desktop_entry = installed
    command = ["gtk-launch", "Talon"] if desktop_entry and shutil.which("gtk-launch") else [str(path)]
    spawn(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL, start_new_session=True)
    return "launched"
