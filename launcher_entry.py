"""Register the authenticated Tlon Web action with the Omarchy app launcher."""

import os
from pathlib import Path
import tempfile

from session_store import SHIP


FILENAME = "urbit-dms-tlon-web.desktop"
MARKER = "X-Omarchy-Urbit-Dms=true"


def launcher_path(data_home=None):
    base = Path(data_home) if data_home is not None else Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return base / "applications" / FILENAME


def desktop_arg(value):
    value = str(value)
    if any(char in value for char in "\r\n\0"):
        raise ValueError("Invalid application launcher path")
    for old, new in (("\\", "\\\\"), ('"', '\\"'), ("`", "\\`"), ("$", "\\$"), ("%", "%%")):
        value = value.replace(old, new)
    return '"' + value + '"'


def owned_launcher(path):
    if not path.exists():
        return True
    try:
        return MARKER in path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False


def install_launcher(ship, script, data_home=None):
    if not isinstance(ship, str) or not SHIP.fullmatch(ship):
        raise ValueError("Invalid ship for launcher")
    script = Path(script).absolute()
    path = launcher_path(data_home)
    if not owned_launcher(path):
        return False  # Never overwrite an unrelated launcher with this name.
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ("[Desktop Entry]\nType=Application\nVersion=1.0\n"
            f"Name=Tlon Web ({ship})\n"
            "Comment=Open Tlon Web with the saved Urbit DM session\n"
            "GenericName=Urbit web client\n"
            f"Exec=python3 {desktop_arg(script)}\n"
            "Icon=internet-group-chat\nCategories=Network;InstantMessaging;\n"
            "Terminal=false\nStartupNotify=true\n" + MARKER + "\n")
    fd, temporary = tempfile.mkstemp(prefix=".urbit-dms-launcher-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def remove_launcher(data_home=None):
    path = launcher_path(data_home)
    if path.is_file() and owned_launcher(path):
        path.unlink()
