"""Open Tlon Web in an isolated Chromium session using the saved Eyre cookie.

The cookie arrives on stdin, never on the command line, in a URL, or in a
browser extension. CDP travels over inherited pipes (no debug TCP port).
The browser context is off-the-record; the temporary profile holds no auth.
This helper stays alive until the window closes, then removes that profile.
"""

import json
from datetime import datetime
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from session_store import valid_session


class BrowserError(ValueError):
    pass


def default_chromium():
    try:
        desktop = subprocess.run(["xdg-settings", "get", "default-web-browser"],
                                 capture_output=True, text=True, timeout=4, check=False).stdout.strip().lower()
    except (OSError, subprocess.TimeoutExpired):
        desktop = ""
    names = ("brave-browser", "brave", "chromium", "google-chrome", "microsoft-edge", "vivaldi", "opera")
    if not desktop.endswith(".desktop") or not any(desktop == name + ".desktop" or desktop.startswith(name + "-") for name in names):
        raise BrowserError("Authenticated browser launch needs a Chromium-based default browser")
    for prefix, binary in (("brave", "brave"), ("chromium", "chromium"),
                           ("google-chrome", "google-chrome-stable"), ("microsoft-edge", "microsoft-edge-stable"),
                           ("vivaldi", "vivaldi"), ("opera", "opera")):
        if desktop.startswith(prefix) and (path := shutil.which(binary)):
            return path
    raise BrowserError("Default Chromium browser executable was not found")


class DevtoolsPipe:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.buffer = b""
        self.sequence = 0

    def call(self, method, params=None, session=None, timeout=12):
        self.sequence += 1
        ident = self.sequence
        command = {"id": ident, "method": method, "params": params or {}}
        if session:
            command["sessionId"] = session
        packet = json.dumps(command).encode() + b"\0"
        while packet:
            try:
                packet = packet[os.write(self.writer, packet):]
            except OSError:
                raise BrowserError("Browser cookie handoff disconnected") from None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if b"\0" in self.buffer:
                frame, _, self.buffer = self.buffer.partition(b"\0")
                try:
                    response = json.loads(frame)
                except (ValueError, UnicodeDecodeError):
                    raise BrowserError("Browser returned an invalid handoff response") from None
                if response.get("id") == ident:
                    if response.get("error") or not isinstance(response.get("result"), dict):
                        raise BrowserError("Browser rejected the authenticated handoff")
                    return response["result"]
                continue  # Browser events can arrive between command responses.
            ready, _, _ = select.select([self.reader], [], [], max(0, deadline - time.monotonic()))
            if ready:
                chunk = os.read(self.reader, 65536)
                if not chunk or len(self.buffer) + len(chunk) > 4_000_000:
                    raise BrowserError("Browser cookie handoff disconnected")
                self.buffer += chunk
        raise BrowserError("Browser did not respond to the authenticated handoff")


def launch(session):
    valid_session(session)
    browser = default_chromium()
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime or not Path(runtime).is_dir():
        runtime = "/tmp/opencode" if Path("/tmp/opencode").is_dir() else tempfile.gettempdir()
    with tempfile.TemporaryDirectory(prefix="omarchy-urbit-dms-browser-", dir=runtime) as profile:
        command_read, command_write = os.pipe()
        reply_read, reply_write = os.pipe()

        def child_pipes():
            os.dup2(command_read, 3)
            os.dup2(reply_write, 4)
            os.set_inheritable(3, True)
            os.set_inheritable(4, True)

        process = None
        try:
            process = subprocess.Popen([
                browser, "--user-data-dir=" + profile, "--remote-debugging-pipe",
                "--no-first-run", "--no-default-browser-check", "--no-startup-window",
                "--disable-extensions",
            ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, close_fds=False, preexec_fn=child_pipes)
            os.close(command_read)
            os.close(reply_write)
            command_read = reply_write = -1
            pipe = DevtoolsPipe(reply_read, command_write)
            context = pipe.call("Target.createBrowserContext", {"disposeOnDetach": True})["browserContextId"]
            url = session["url"] + "/apps/groups"
            pipe.call("Storage.setCookies", {"browserContextId": context, "cookies": [{
                "name": session["cookieName"], "value": session["cookieValue"],
                "url": session["url"] + "/", "path": "/", "httpOnly": True,
                "secure": urlsplit(session["url"]).scheme == "https", "sameSite": "Lax",
                **({"expires": datetime.fromisoformat(session["expires"]).timestamp()} if "expires" in session else {}),
            }]})
            # Verify the cookie landed in the off-record context, not in the
            # browser's default profile. Never print or return its value.
            cookies = pipe.call("Storage.getCookies", {"browserContextId": context}).get("cookies", [])
            if not any(item.get("name") == session["cookieName"] for item in cookies):
                raise BrowserError("Browser did not accept the ship session")
            target = pipe.call("Target.createTarget", {"url": "about:blank", "browserContextId": context})["targetId"]
            attached = pipe.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
            pipe.call("Page.navigate", {"url": url}, session=attached)
            pipe.call("Target.activateTarget", {"targetId": target})
            print("ready", flush=True)
            # Keep CDP connected while the window lives; disposing this
            # context (or closing the pipe) revokes its in-memory cookie.
            while process.poll() is None:
                time.sleep(1)
                try:
                    targets = pipe.call("Target.getTargets", timeout=4).get("targetInfos", [])
                except BrowserError:
                    break
                if not any(item.get("targetId") == target for item in targets):
                    break
        finally:
            for fd in (command_read, command_write, reply_read, reply_write):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    try:
        launch(json.loads(sys.stdin.readline()))
    except (BrowserError, ValueError, KeyError, OSError) as error:
        print("error: " + (str(error) if isinstance(error, BrowserError) else "Could not open authenticated browser"), flush=True)
