"""Origin-bound Urbit session storage via the desktop Secret Service.

The +code is never saved. Only nonsecret account metadata goes in XDG state;
the Eyre cookie goes to the unlocked keyring via secret-tool's stdin.
"""

import http.cookiejar
import contextlib
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit


from instance import INSTANCE


APPLICATION = "omarchy-urbit-dms"
SHIP = re.compile(r"~[a-z-]{1,100}\Z")
COOKIE = re.compile(r"[A-Za-z0-9._~-]{1,4096}\Z")


class StoreError(ValueError):
    pass


class ExistingAccountError(StoreError):
    pass


def origin(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise StoreError("Invalid saved ship origin")
    value = value.strip().rstrip("/")
    if not value.startswith(("https://", "http://")):
        value = "https://" + value
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.port == 0
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                or any(ord(char) < 33 for char in value)):
            raise StoreError("Enter a ship URL without a path or credentials")
    except ValueError:
        raise StoreError("Enter a valid ship URL") from None
    return value


def valid_session(session, url=None, ship=None, check_expiry=True):
    if (not isinstance(session, dict) or set(session) not in ({"url", "ship", "cookieName", "cookieValue"}, {"url", "ship", "cookieName", "cookieValue", "expires"})
            or not isinstance(session["url"], str) or origin(session["url"]) != session["url"]
            or not isinstance(session["ship"], str) or not SHIP.fullmatch(session["ship"])
            or session["cookieName"] != "urbauth-" + session["ship"]
            or not isinstance(session["cookieValue"], str) or not COOKIE.fullmatch(session["cookieValue"])
            or (url is not None and session["url"] != url)
            or (ship is not None and session["ship"] != ship)):
        raise StoreError("Saved Urbit session does not match this ship. Sign in again.")
    if check_expiry and "expires" in session:
        try:
            expiry = datetime.fromisoformat(session["expires"])
            if expiry.tzinfo is None or datetime.now(timezone.utc) >= expiry:
                raise ValueError()
        except (ValueError, TypeError):
            raise StoreError("Saved Urbit session expired; sign in again") from None
    return session


def cookie_for(session):
    valid_session(session)
    parsed = urlsplit(session["url"])
    expiry = datetime.fromisoformat(session["expires"]).timestamp() if "expires" in session else None
    return http.cookiejar.Cookie(
        version=0, name=session["cookieName"], value=session["cookieValue"],
        port=None, port_specified=False, domain=parsed.hostname, domain_specified=False,
        domain_initial_dot=False, path="/", path_specified=True,
        secure=parsed.scheme == "https", expires=expiry, discard=expiry is None,
        comment=None, comment_url=None, rest={}, rfc2109=False)


class SessionStore:
    def __init__(self, root=None, runner=subprocess.run, instance=INSTANCE):
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        self.root = Path(root) if root is not None else base / instance.state_name
        self.runner = runner
        self.instance = instance

    def _directory(self):
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.root.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise OSError()
            os.chmod(self.root, 0o700)
        except OSError:
            raise StoreError("Private Urbit account state is unavailable") from None

    @contextlib.contextmanager
    def locked(self):
        self._directory()
        try:
            fd = os.open(self.root / "account.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise OSError()
                os.fchmod(stream.fileno(), 0o600)
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
        except OSError:
            raise StoreError("Another connection operation is running, or private state is unavailable") from None

    def account(self):
        self._directory()
        try:
            fd = os.open(self.root / "account.json", os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError:
            raise StoreError("Saved Urbit account cannot be read") from None
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise StoreError("Saved Urbit account is not a private file")
            os.fchmod(stream.fileno(), 0o600)
            raw = stream.read(4097)
        try:
            value = json.loads(raw) if len(raw) <= 4096 else None
            if (not isinstance(value, dict) or set(value) not in ({"version", "url", "ship"}, {"version", "url", "ship", "application_request_id"})
                    or value["version"] != 1 or not isinstance(value["ship"], str)
                    or not SHIP.fullmatch(value["ship"]) or origin(value["url"]) != value["url"]):
                raise ValueError()
        except (ValueError, UnicodeError, TypeError):
            raise StoreError("Saved Urbit account is invalid; sign in again") from None
        if "application_request_id" in value and (not isinstance(value["application_request_id"], str) or not re.fullmatch("[0-9a-f]{32}", value["application_request_id"])):
            raise StoreError("Invalid application connection account")
        return value

    def _secret(self, action, url, data=b""):
        args = ["secret-tool", action]
        if action == "store":
            args.append("--label=" + self.instance.label)
        args += ["application", self.instance.state_name, "origin", url]
        try:
            result = self.runner(args, input=data, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise StoreError("Secret Service is unavailable or locked; unlock your keyring and retry") from None
        if result.returncode != 0:
            raise StoreError("Secret Service is unavailable or locked; unlock your keyring and retry")
        if len(result.stdout) > 16384:
            raise StoreError("Saved Urbit session is invalid; sign in again")
        return result.stdout

    def lookup(self, url, ship, check_expiry=True):
        raw = self._secret("lookup", url)
        try:
            return valid_session(json.loads(raw), url, ship, check_expiry=check_expiry)
        except (ValueError, TypeError, UnicodeError):
            raise StoreError("Saved Urbit session is invalid; sign in again") from None

    def save(self, session):
        with self.locked():
            self._save(session)

    def save_new(self, session):
        with self.locked():
            if self.account() is not None:
                raise ExistingAccountError("Disconnect the existing account before connecting another")
            self._save(session)

    def _save(self, session, application_request_id=None):
        valid_session(session)
        if application_request_id is not None and (not isinstance(application_request_id, str) or not re.fullmatch('[0-9a-f]{32}', application_request_id)):
            raise StoreError('Invalid application connection account')
        metadata = {"version": 1, "url": session["url"], "ship": session["ship"]}
        if application_request_id is not None:
            metadata['application_request_id'] = application_request_id
        self._secret("store", session["url"], json.dumps(session).encode())
        if self.lookup(session["url"], session["ship"]) != session:
            raise StoreError("Could not verify saved Urbit session")
        self._directory()
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".account-", dir=self.root)
            with os.fdopen(fd, "wb") as stream:
                stream.write(json.dumps(metadata).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.root / "account.json")
            tmp = None
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            raise StoreError("Could not save private Urbit account state") from None
        finally:
            if tmp is not None:
                os.unlink(tmp)

    def forget(self):
        with self.locked():
            self._forget()

    def _forget(self):
        account = self.account()
        if account is None:
            return
        self._secret("clear", account["url"])
        try:
            (self.root / "account.json").unlink()
        except OSError:
            raise StoreError("Could not remove saved Urbit account") from None
