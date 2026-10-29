"""Register the authenticated Tlon Web action with the Omarchy app launcher."""

import os
import re
from pathlib import Path
import tempfile

from session_store import SHIP
from instance import INSTANCE, CANONICAL_ID


FILENAME = "urbit-dms-tlon-web.desktop"
MARKER = "X-Omarchy-Urbit-Dms=true"


def instance_suffix(instance):
    profile = instance.profile
    if not isinstance(profile, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", profile):
        raise ValueError("Invalid launcher profile")
    suffix = "" if profile == "default" else "." + profile
    if instance.plugin_id != CANONICAL_ID + suffix:
        raise ValueError("Launcher instance does not match its profile")
    return suffix


def launcher_path(data_home=None, instance=INSTANCE):
    base = Path(data_home) if data_home is not None else Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return base / "applications" / (FILENAME.removesuffix(".desktop") + instance_suffix(instance) + ".desktop")


def desktop_arg(value):
    value = str(value)
    if any(char in value for char in "\r\n\0"):
        raise ValueError("Invalid application launcher path")
    for old, new in (("\\", "\\\\"), ('"', '\\"'), ("`", "\\`"), ("$", "\\$"), ("%", "%%")):
        value = value.replace(old, new)
    return '"' + value + '"'


def owned_launcher(path, instance=INSTANCE):
    if not path.exists():
        return True
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        owners = [line.removeprefix("X-Omarchy-Urbit-Dms-Instance=") for line in lines
                  if line.startswith("X-Omarchy-Urbit-Dms-Instance=")]
        return MARKER in lines and (owners == [instance.plugin_id] or
                                   (not owners and instance.profile == "default"))
    except (OSError, UnicodeError):
        return False


def install_launcher(ship, script, data_home=None, instance=INSTANCE):
    if not isinstance(ship, str) or not SHIP.fullmatch(ship):
        raise ValueError("Invalid ship for launcher")
    script = Path(script).absolute()
    path = launcher_path(data_home, instance)
    if not owned_launcher(path, instance):
        return False  # Never overwrite an unrelated launcher with this name.
    path.parent.mkdir(parents=True, exist_ok=True)
    label = "" if instance.profile == "default" else " [" + instance.profile + "]"
    text = ("[Desktop Entry]\nType=Application\nVersion=1.0\n"
            f"Name=Tlon Web ({ship}){label}\n"
            "Comment=Open Tlon Web with the saved Urbit DM session\n"
            "GenericName=Urbit web client\n"
            f"Exec=python3 {desktop_arg(script)}\n"
            "Icon=internet-group-chat\nCategories=Network;InstantMessaging;\n"
            "Terminal=false\nStartupNotify=true\n" + MARKER + "\n"
            + "X-Omarchy-Urbit-Dms-Instance=" + instance.plugin_id + "\n")
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


def remove_launcher(data_home=None, instance=INSTANCE):
    path = launcher_path(data_home, instance)
    if path.is_file() and owned_launcher(path, instance):
        path.unlink()
