#!/usr/bin/env python3
"""Private JSON-lines bridge between Quickshell and a running %groups desk.

One process owns the live cookie and Eyre channel. The session cookie survives
shell restarts in Secret Service; the +code is never saved or logged.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import collections
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import select
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from session_store import SessionStore, StoreError, cookie_for, origin, valid_session
from external_apps import launch_talon, talon_appimage
from launcher_entry import install_launcher, remove_launcher
from eyre_session import NoRedirect, revoke
from outbound_journal import OutboundJournal
from effects_journal import EffectsJournal
from transport_journal import TransportJournal

DA_UNIX_EPOCH = 170141184475152167957503069145530368000
DA_SECOND = 1 << 64
PATP = re.compile(r"^~[a-z-]+$")
# Some ship gateways (including Cloudflare in front of a user's ship) reject
# Python-urllib's default user-agent before the request reaches Eyre.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def patp(value: str) -> str:
    value = value.strip().lower()
    if not value.startswith("~"):
        value = "~" + value
    if not PATP.fullmatch(value) or len(value) > 100 or "--" in value:
        raise ValueError("Enter an Urbit ID such as ~sampel-palnet")
    return value


def post_id(author: str, milliseconds: int) -> str:
    atom = DA_UNIX_EPOCH + milliseconds * DA_SECOND // 1000
    return author + "/" + f"{atom:,}".replace(",", ".")


def text_of(content: object) -> str:
    """A readable preview of Tlon's Story; unsupported rich blocks stay visible."""
    if not isinstance(content, list):
        return ""

    def inline(items: list) -> str:
        result = []
        for item in items:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                for variant in ("bold", "italics", "strike", "blockquote"):
                    if isinstance(item.get(variant), list):
                        result.append(inline(item[variant]))
                        break
                else:
                    if "break" in item:
                        result.append("\n")
                    elif "link" in item:
                        link = item["link"]
                        result.append(str(link.get("content") or link.get("href") or "") if isinstance(link, dict) else "")
                    elif "task" in item and isinstance(item["task"], dict):
                        result.append(("[x] " if item["task"].get("checked") else "[ ] ") + inline(item["task"].get("content") or []))
                    else:
                        result.append(str(item.get("text") or item.get("inline-code") or item.get("code") or item.get("ship") or item.get("tag") or ""))
        return "".join(result)

    parts = []
    for verse in content:
        if not isinstance(verse, dict):
            continue
        if isinstance(verse.get("inline"), list):
            parts.append(inline(verse["inline"]))
        elif "block" in verse:
            parts.append("[attachment]")
    return "\n".join(parts).strip()


def message_of(value: object, key: str = "") -> dict | None:
    if not isinstance(value, dict) or value.get("type") == "tombstone":
        return None
    essay = value.get("essay")
    if not isinstance(essay, dict):
        return None
    seal = value.get("seal") or {}
    kind = essay.get("kind") or ""
    accepted_ship = ""
    if kind == "/chat/notice":
        content = essay.get("content")
        if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict):
            inline = content[0].get("inline")
            # %chat emits this structured notice when a DM invitation is
            # accepted. A regular message containing the same words is not it.
            if (isinstance(inline, list) and len(inline) == 2 and
                    isinstance(inline[0], dict) and isinstance(inline[0].get("ship"), str) and
                    isinstance(inline[1], str) and inline[1] == " joined the chat"):
                accepted_ship = inline[0]["ship"]
    return {
        "id": seal.get("id") or key,
        "author": essay.get("author") if isinstance(essay.get("author"), str) else
                  (essay.get("author") or {}).get("ship", ""),
        "sent": essay.get("sent", 0),
        "text": text_of(essay.get("content")),
        "kind": kind,
        "acceptedShip": accepted_ship,
    }


def merge_contacts(book: object, directory: object) -> dict[str, str]:
    def name(profile: object) -> str:
        if not isinstance(profile, dict):
            return ""
        field = profile.get("nickname") or {}
        value = str(field.get("value", "")) if isinstance(field, dict) else ""
        # Some contact books carry a display label like "~ship (nickname)".
        # The UI can show the patp separately; keep only its human nickname.
        match = re.fullmatch(r"~[a-z-]+\s+\(([^()]+)\)", value.strip())
        return match.group(1) if match else value

    result: dict[str, str] = {}
    if isinstance(directory, dict):
        for ship, entry in directory.items():
            if ship.startswith("~") and isinstance(entry, dict):
                result[ship] = name(entry.get("mod")) or name(entry.get("contact"))
    if isinstance(book, dict):
        for ship, entry in book.items():
            if ship.startswith("~") and isinstance(entry, list) and len(entry) == 2:
                result[ship] = name(entry[1]) or name(entry[0]) or result.get(ship, "")
    return result


def inbox_of(peers: object, heads: object, contacts: dict[str, str], archived: object = ()) -> list[dict]:
    if isinstance(heads, dict):
        heads = heads.get("dms")
    if not isinstance(peers, list) or not isinstance(heads, list) or not isinstance(archived, (list, tuple)):
        raise ValueError("This ship did not return a compatible DM list")
    head_map = {h["whom"]: h for h in heads
                if isinstance(h, dict) and isinstance(h.get("whom"), str)}
    rows = []
    active = {peer for peer in peers if isinstance(peer, str)}
    seen: set[str] = set()
    for peer in [*peers, *archived]:
        if not isinstance(peer, str) or not peer.startswith("~"):
            continue  # clubs are intentionally out of scope
        if peer in seen:
            continue
        seen.add(peer)
        head = head_map.get(peer, {})
        latest = message_of(head.get("latest")) or {}
        is_archived = peer not in active
        rows.append({"ship": peer, "name": contacts.get(peer) or peer,
                      "recency": max(int(head.get("recency") or 0), int(latest.get("sent") or 0)),
                      "preview": latest.get("text") or ("Archived conversation" if is_archived else ""),
                      "latestAuthor": latest.get("author", ""), "latestKind": latest.get("kind", ""),
                      "archived": is_archived})
    return sorted(rows, key=lambda row: (-row["recency"], row["ship"]))


def parse_page(page: object) -> dict:
    if not isinstance(page, dict) or not isinstance(page.get("writs"), dict):
        raise ValueError("This ship did not return compatible DM history")
    messages = [msg for key, writ in page["writs"].items()
                if (msg := message_of(writ, key))]
    messages.sort(key=lambda msg: (int(msg["sent"] or 0), str(msg["id"])))
    return {"messages": messages, "older": page.get("older")}


def dm_unreads(activity: object) -> dict[str, int]:
    """Use the DM stream's own unread count, excluding child reply threads."""
    if not isinstance(activity, dict):
        raise ValueError("This ship did not return compatible activity summaries")
    result: dict[str, int] = {}
    for source, summary in activity.items():
        if not source.startswith("ship/~") or not isinstance(summary, dict):
            continue
        point = summary.get("unread")
        if isinstance(point, dict):
            result[source.removeprefix("ship/")] = max(0, int(point.get("count") or 0))
    return result


class Bridge:
    def __init__(self, session_store=None, outbound_journal=None) -> None:
        self.output_lock = threading.Lock()
        self.auth_lock = threading.Lock()
        self.channel_lock = threading.RLock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=10)
        self.refresh_lock = threading.Lock()
        self.directory_lock = threading.Lock()
        self.last_refresh = 0.0
        self.directory_loaded = False
        self.activity_subscription = 0
        self.recent_notifications: collections.deque[str] = collections.deque(maxlen=256)
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPCookieProcessor(self.cookies))
        self.opener.addheaders = [("User-Agent", USER_AGENT)]
        self.base = ""
        self.ship = ""
        self.channel = ""
        self.generation = 0
        self.request_id = 0
        self.pending: dict[int, tuple[threading.Event, dict]] = {}
        self.closing = False
        self.listeners = []
        self.stream_sockets = {}
        self.contacts: dict[str, str] = {}
        self.unreads: dict[str, int] = {}
        self.book: dict = {}
        self.directory: dict = {}
        self.session_store = session_store if session_store is not None else SessionStore()
        self.manage_launcher = session_store is None
        self.outbound = outbound_journal if outbound_journal is not None else OutboundJournal()
        self.effects = EffectsJournal(self.outbound.store.root)
        self.transport = TransportJournal(self.outbound.store.root)
        self.transport_context = self.transport_lease = None
        self.channel_record = None
        self.transport_session = None
        self.account_request_id = None

    def emit(self, event: str, **data: object) -> None:
        with self.output_lock:
            print(json.dumps({"event": event, **data}, ensure_ascii=False), flush=True)

    def request(self, path: str, data: bytes | None = None, method: str | None = None):
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        with self.opener.open(req, timeout=25) as response:
            return response.read()

    def scry(self, agent: str, path: str):
        return json.loads(self.request("/~/scry/" + agent + path + ".json"))

    def command(self, payload: dict) -> None:
        action = payload.get("action")
        request_id = payload.get("request")
        try:
            if self.closing and action != "outbound-status":
                raise ValueError("DMs is stopping its connection; new work is paused")
            if action == "outbound-status":
                self.emit_outbound()
            elif action == "login":
                with self.auth_lock:
                    result = self.login(payload.get("url", ""), payload.get("code", ""))
                self.emit("login", request=request_id, ship=result)
                self.pool.submit(self.refresh)
            elif action == "check-account":
                with self.auth_lock:
                    self.check_account()
            elif action in ("restore", "refresh"):
                # A separately approved receiver can add an account while this
                # bridge is already running. Opening/refreshing the plugin picks
                # it up without another +code or replacing an active session.
                with self.auth_lock:
                    if self.check_account():
                        return  # Show the revoked state before any later explicit restore.
                    result = self.restore() if not self.ship else None
                if result:
                    self.emit("login", ship=result, restored=True)
                if action == "refresh":
                    self.refresh(force=True)
                elif result:
                    self.pool.submit(self.refresh)
            elif action == "logout":
                with self.auth_lock:
                    revocation = self.logout()
                self.emit("logout", request=request_id, revocation=revocation)
            elif action == "contacts":
                self.load_directory()
            elif action == "open":
                ship = patp(payload.get("ship", ""))
                self.open_dm(ship, payload.get("cursor"))
            elif action == "accept":
                self.accept(patp(payload.get("ship", "")))
            elif action == "send":
                ship = patp(payload.get("ship", ""))
                self.send(ship, str(payload.get("text", "")), request_id)
            elif action == "open-browser":
                self.open_browser()
                self.emit("opened-app", request=request_id, name="Tlon Web")
            elif action == "launch-talon":
                result = launch_talon()
                self.emit("opened-app", request=request_id, name="Talon", result=result)
            elif action == "apps":
                self.emit("apps", talon=talon_appimage() is not None)
            else:
                raise ValueError("Unknown command")
        except Exception as error:
            if isinstance(error, urllib.error.HTTPError):
                reason = f"Ship returned HTTP {error.code}"
            elif isinstance(error, ValueError):
                reason = str(error)
            else:
                reason = "Ship unavailable; check the URL and connection"
            self.emit("error", request=request_id, action=action, message=reason)
        finally:
            if action in ("send", "open", "accept"):
                self.emit_outbound()

    def emit_outbound(self):
        with self.channel_lock:
            try:
                self.emit("outbound-state", unresolved=self.outbound.unresolved(), unresolvedEffects=self.effects.unresolved(), readable=True)
            except (OSError, ValueError):
                self.emit("outbound-state", unresolved=[], unresolvedEffects=[], readable=False)

    def ensure_transport_owner(self):
        if self.transport_lease is None:
            context = self.transport.owner()
            lease = context.__enter__()
            self.transport_context, self.transport_lease = context, lease

    def release_transport_owner(self):
        if self.transport_context is not None:
            self.transport_context.__exit__(None, None, None)
            self.transport_context = self.transport_lease = None

    def delete_transport_record(self, row):
        """Exact direct-to-Eyre cleanup; no application write is reconstructed."""
        # Even a failed local cleanup write must pause further application work.
        # The durable row remains authoritative for the next exact cleanup retry.
        if self.channel_record is not None and self.channel_record["id"] == row["id"]:
            self.channel_record = {**self.channel_record, "phase": "closing"}
        closing = self.transport.prepare_cleanup(self.transport_lease, row["id"], self.transport_session)
        if closing["phase"] == "closed":
            return closing
        if self.channel_record is not None and self.channel_record["id"] == row["id"]:
            self.channel_record = closing
        # prepare_cleanup verified this session's origin, ship and fingerprint.
        # Keep the credentials only in the live jar; never print or persist them.
        request = urllib.request.Request(closing["origin"] + "/~/channel/" + closing["channel"],
                    data=b'[{"action":"delete"}]', method="PUT",
                    headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=25) as response:
                status = response.status
                if status not in (200, 204) or response.read(1025):
                    raise StoreError("Channel cleanup did not return an exact successful response")
        except urllib.error.HTTPError as error:
            if error.code not in (401, 403):
                raise
            # This credential no longer authorizes this exact channel. Record
            # invalidation, not a successful delete or a successful message.
            return self.transport.invalidated(self.transport_lease, row["id"], closing["cleanup_request"], error.code)
        return self.transport.deleted(self.transport_lease, row["id"], closing["cleanup_request"], status)

    def new_channel(self):
        self.ensure_transport_owner()
        # A prior process may have died before receiving its subscribe/delete
        # response. Reconcile only its exact channel before creating another.
        for row in self.transport.status()["channels"]:
            if row["phase"] != "closed":
                self.delete_transport_record(row)
        self.channel_record = self.transport.begin(self.transport_lease, self.transport_session)
        self.channel = self.channel_record["channel"]

    @contextlib.contextmanager
    def owned_stream(self, response, binding):
        # Keep an independent descriptor for cancellation. Closing a buffered
        # HTTPResponse from another thread can wait on the blocked reader lock;
        # shutting down this exact socket wakes that reader without fd reuse.
        descriptor = os.dup(response.fileno())
        try:
            control = socket.socket(fileno=descriptor)
        except Exception:
            os.close(descriptor)
            raise
        try:
            with self.channel_lock:
                if self.closing or binding != (self.generation, self.channel):
                    control.shutdown(socket.SHUT_RDWR)
                    raise ValueError("Discarded an old connection stream")
                if binding in self.stream_sockets:
                    raise ValueError("This channel already has a stream reader")
                self.stream_sockets[binding] = control
            yield
        finally:
            with self.channel_lock:
                if self.stream_sockets.get(binding) is control:
                    del self.stream_sockets[binding]
                control.close()

    def cancel_streams(self, binding=None):
        with self.channel_lock:
            for key, control in self.stream_sockets.items():
                if binding is None or key == binding:
                    try:
                        control.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        # Process/thread exit is still verified before closure
                        # can be reported; a failed shutdown is never proof.
                        pass

    def close_channel(self):
        if not self.channel:
            return
        if self.channel_record is not None:
            self.delete_transport_record(self.channel_record)
            self.channel_record = None
        else:
            # Existing in-memory/test callers can still supply a channel. Every
            # production start/reconnect now obtains one through new_channel().
            self.channel_action({"action": "delete"})
        self.cancel_streams((self.generation, self.channel))
        self.channel = ""

    def reset_runtime(self) -> None:
        # Refuse account replacement if the previous channel cannot be closed;
        # otherwise its credential could be overwritten before recovery.
        with self.channel_lock:
            if self.transport_lease is not None and self.transport_session is not None:
                for row in self.transport.status()["channels"]:
                    if row["phase"] != "closed":
                        self.delete_transport_record(row)
                self.channel_record = None
            elif self.channel:
                try:
                    self.channel_action({"action": "delete"})
                except (OSError, urllib.error.URLError):
                    pass
            self.cancel_streams()
            self.generation += 1
            self.base = self.ship = self.channel = ""
            self.transport_session = None
            self.account_request_id = None
            self.contacts = self.book = self.directory = self.unreads = {}
            self.directory_loaded = False
            self.last_refresh = 0.0
            self.recent_notifications.clear()
            self.cookies.clear()

    def open_browser(self) -> None:
        if not self.ship or not self.base:
            raise ValueError("Sign in before opening Tlon Web")
        session = self.session_store.lookup(self.base, self.ship)
        self.cookies.set_cookie(cookie_for(session))
        try:
            self.scry("chat", "/dm")
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise StoreError("Saved Urbit session expired; sign in again with +code") from None
            raise
        helper = subprocess.Popen(
            [sys.executable, os.path.join(os.path.dirname(__file__), "browser_session.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True)
        try:
            helper.stdin.write(json.dumps(session).encode() + b"\n")
            helper.stdin.close()
            ready, _, _ = select.select([helper.stdout], [], [], 25)
            reply = helper.stdout.readline().decode("utf-8", "replace").strip() if ready else ""
            if reply != "ready":
                # The helper never prints the cookie, even on failure.
                raise ValueError(reply.removeprefix("error: ") if reply.startswith("error: ")
                                 else "Could not open authenticated browser")
            helper.stdout.close()
            threading.Thread(target=helper.wait, daemon=True).start()
        except Exception:
            if helper.poll() is None:
                helper.terminate()
            helper.stdout.close()
            threading.Thread(target=helper.wait, daemon=True).start()
            raise

    def logout(self) -> str:
        # Retain credentials on uncertain network/keyring outcomes so retry is
        # possible. Never send all/sid: this is only the plugin's own session.
        account = self.session_store.account()
        outcome = "not-stored"
        if account is not None:
            session = self.session_store.lookup(account["url"], account["ship"], check_expiry=False)
            self.ensure_transport_owner()
            if self.transport_session is None and any(row["phase"] != "closed" for row in self.transport.status()["channels"]):
                self.base, self.ship = session["url"], session["ship"]
                self.cookies.set_cookie(cookie_for(session))
                self.transport_session = dict(session)
            self.reset_runtime()
            outcome = revoke(session)
        self.session_store.forget()
        self.reset_runtime()
        if self.manage_launcher:
            try:
                remove_launcher()
            except OSError:
                pass  # The credential is already gone; never undo sign-out.
        return outcome

    def sync_launcher(self) -> None:
        if not self.manage_launcher:
            return
        try:
            installed = install_launcher(self.ship, Path(__file__).absolute().with_name("open_web.py"))
            self.emit("launcher", installed=installed,
                      message="" if installed else "A different application already uses the Tlon Web launcher name")
        except (OSError, ValueError):
            self.emit("launcher", installed=False, message="Could not add Tlon Web to the application launcher")

    def start_session(self) -> None:
        if self.closing:
            raise ValueError("DMs is stopping its connection")
        with self.channel_lock:
            try:
                self.new_channel()
            except Exception:
                self.ship = ""
                raise
        generation = self.generation
        if self.closing:
            raise ValueError("DMs is stopping its connection")
        self.listeners = [t for t in self.listeners if t.is_alive()]
        listener = threading.Thread(target=self.listen, args=(generation,), daemon=True)
        self.listeners.append(listener)
        listener.start()

    def check_account(self):
        """Notice receiver cleanup without reusing a revoked in-memory session."""
        if not self.ship:
            return
        account = self.session_store.account()
        if (account is not None and (account["url"], account["ship"]) == (self.base, self.ship)
                and account.get("application_request_id") == self.account_request_id):
            return
        # Invalidate visible state immediately; keep unknown sends in their
        # journals. Cleanup uses only the old exact channel/session binding.
        self.emit("logout", revoked=True)
        try:
            self.reset_runtime()
        except (OSError, ValueError, urllib.error.URLError):
            with self.channel_lock:
                self.cancel_streams()
                self.generation += 1
                self.base = self.ship = self.channel = ""
                self.contacts = self.book = self.directory = self.unreads = {}
                self.cookies.clear()
            # The durable cleanup row and transport_session remain for review.
        return True

    def restore(self) -> str | None:
        account = self.session_store.account()
        if account is None:
            return None
        session = self.session_store.lookup(account["url"], account["ship"])
        self.ensure_transport_owner()
        self.reset_runtime()
        self.base = session["url"]
        self.cookies.set_cookie(cookie_for(session))
        try:
            self.scry("chat", "/dm")
        except urllib.error.HTTPError as error:
            self.reset_runtime()
            if error.code in (401, 403):
                raise StoreError("Saved Urbit session expired; sign in again with +code") from None
            raise StoreError(f"Saved Urbit session could not reach DMs (HTTP {error.code})") from None
        except Exception:
            self.reset_runtime()
            raise
        self.ship = session["ship"]
        self.account_request_id = account.get("application_request_id")
        self.transport_session = dict(session)
        self.sync_launcher()
        self.start_session()
        return self.ship

    def login(self, url: str, code: str) -> str:
        url = origin(url)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Enter a ship URL without a path or credentials")
        if not code.strip().removeprefix("+"):
            raise ValueError("Enter the code returned by +code")
        self.ensure_transport_owner()
        if self.transport_session is None and any(row["phase"] != "closed" for row in self.transport.status()["channels"]):
            raise StoreError("Restore the existing connection to finish channel cleanup before signing in again")
        self.reset_runtime()
        self.base = url
        body = urllib.parse.urlencode({"password": code.strip().removeprefix("+")}).encode()
        req = urllib.request.Request(url + "/~/login", data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self.opener.open(req, timeout=25) as response:
                response.read()
        except urllib.error.HTTPError as error:
            raise ValueError(f"Login rejected (HTTP {error.code}). Check the access key and ship URL") from None
        try:
            cookies = [c for c in self.cookies if c.name.startswith("urbauth-~")]
            if len(cookies) != 1:
                raise ValueError("Login failed: the ship did not return one unambiguous session")
            cookie = cookies[0]
            # CookieJar prefixes explicit Domain attributes with a dot, including
            # Domain equal to this host. Accept that spelling, not parent domains.
            domain = cookie.domain.lstrip(".").lower()
            host_only = (not cookie.domain_specified and
                         cookie.domain == http.cookiejar.eff_request_host(req)[1])
            if ((domain != parsed.hostname and not host_only) or cookie.path != "/"
                    or cookie.is_expired() or (cookie.secure and parsed.scheme != "https")):
                raise ValueError("Login failed: the ship returned a session for a different scope")
            session = valid_session({"url": url, "ship": cookie.name.removeprefix("urbauth-"),
                                     "cookieName": cookie.name, "cookieValue": cookie.value})
        except ValueError:
            self.reset_runtime()
            raise
        self.ship = session["ship"]
        # A guest can receive an urbauth cookie too: verify authenticated
        # access, and distinguish rejected credentials from a missing desk.
        try:
            self.scry("chat", "/dm")
        except urllib.error.HTTPError as error:
            self.reset_runtime()
            if error.code in (401, 403):
                raise ValueError("Login did not grant DM access (HTTP 403). Check your +code") from None
            if error.code == 404:
                raise ValueError("Signed in, but this ship has no compatible %chat agent") from None
            raise ValueError(f"Signed in, but the DM check failed (HTTP {error.code})") from None
        try:
            self.session_store.save(session)
        except StoreError:
            self.reset_runtime()
            raise
        self.transport_session = dict(session)
        self.sync_launcher()
        self.start_session()
        return self.ship

    def channel_action(self, fields: dict, *, binding=None) -> int:
        with self.channel_lock:
            if binding is not None and binding != (self.generation, self.channel):
                raise ValueError("Ship session changed; discarded old channel action")
            if not self.channel:
                raise ValueError("Sign in first")
            if self.channel_record is not None and self.channel_record["phase"] != "open":
                raise StoreError("Connection cleanup is pending; new channel actions are paused")
            self.request_id += 1
            request_id = self.request_id
            body = [{"id": request_id, **fields}]
            self.request("/~/channel/" + self.channel, json.dumps(body).encode(), "PUT")
            return request_id

    def listen(self, generation: int) -> None:
        pause = 1
        while True:
            with self.channel_lock:
                if self.closing or generation != self.generation or not self.channel:
                    return
                binding = (generation, self.channel)
                url = self.base + "/~/channel/" + self.channel
                ship, opener = self.ship[1:], self.opener
            try:
                req = urllib.request.Request(url, headers={"Accept": "text/event-stream", "Accept-Encoding": "identity"})
                # Each mutation is bound to the channel that owns this stream.
                self.channel_action({"action": "subscribe", "ship": ship, "app": "chat", "path": "/v4"}, binding=binding)
                self.channel_action({"action": "subscribe", "ship": ship, "app": "contacts", "path": "/v1/news"}, binding=binding)
                with self.channel_lock:
                    activity = self.channel_action({"action": "subscribe", "ship": ship, "app": "activity", "path": "/v5"}, binding=binding)
                    self.activity_subscription = activity
                self.channel_action({"action": "subscribe", "ship": ship, "app": "chat", "path": "/dm/invited"}, binding=binding)
                with opener.open(req, timeout=65) as response, self.owned_stream(response, binding):
                    pause = 1
                    frame: list[str] = []
                    event_id = None
                    for raw in response:
                        with self.channel_lock:
                            if binding != (self.generation, self.channel):
                                return
                            line = raw.decode("utf-8", "replace").rstrip("\r\n")
                            if line.startswith("id:"):
                                event_id = line[3:].strip()
                            elif line.startswith("data:"):
                                frame.append(line[5:].lstrip())
                            elif not line:
                                if frame:
                                    self.on_fact(json.loads("\n".join(frame)), event_id)
                                frame, event_id = [], None
            except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
                if generation != self.generation:
                    break
            if self.closing or generation != self.generation:
                break
            while True:
                time.sleep(pause)
                pause = min(pause * 2, 30)
                with self.channel_lock:
                    # Selection may change during backoff. Never delete its
                    # channel, or subscribe again while cleanup is uncertain.
                    if self.closing or binding != (self.generation, self.channel):
                        return
                    try:
                        self.close_channel()
                        self.new_channel()
                    except (OSError, urllib.error.URLError, ValueError):
                        # The closing channel and its original request remain
                        # durable. Retry cleanup, never the application actions.
                        binding = (self.generation, self.channel)
                        continue
                break
            try:
                self.refresh()
            except (OSError, urllib.error.URLError, ValueError):
                pass

    def on_fact(self, outer: dict, event_id: str | None) -> None:
        if not isinstance(outer, dict):
            return
        if self.closing and outer.get("response") != "poke":
            return
        with self.channel_lock:
            binding = (self.generation, self.channel)
        if event_id and event_id.isdecimal() and outer.get("response") == "diff":
            self.pool.submit(self.channel_action, {"action": "ack", "event-id": int(event_id)}, binding=binding)
        if outer.get("response") == "poke":
            ident = outer.get("id")
            pending = self.pending.get(ident) if type(ident) is int else None
            if pending and not pending[0].is_set():
                # Missing err is not a positive receipt. Keep an unfamiliar
                # response uncertain, and never replace an already seen result.
                pending[1]["error"] = outer.get("err") is not None
                pending[1]["unknown"] = not pending[1]["error"] and outer.get("ok") != "ok"
                pending[0].set()
            return
        if outer.get("response") == "subscribe" and outer.get("id") == self.activity_subscription and outer.get("err"):
            self.pool.submit(self.channel_action, {"action": "subscribe", "ship": self.ship[1:],
                                                   "app": "activity", "path": "/v4"}, binding=binding)
            return
        if outer.get("response") != "diff":
            return
        fact = outer.get("json")
        if not isinstance(fact, dict):
            return
        if "whom" in fact and "response" in fact and str(fact["whom"]).startswith("~"):
            delta = fact["response"]
            if isinstance(delta, dict) and isinstance(delta.get("add"), dict):
                essay = delta["add"].get("essay", {})
                if isinstance(essay, dict):
                    msg = message_of({"essay": essay, "seal": {"id": fact.get("id", "")}})
                    if msg:
                        self.emit("message", ship=fact["whom"], message=msg)
            return
        if "add" in fact and isinstance(fact["add"], dict):
            event = fact["add"].get("event", {})
            if isinstance(event, dict) and event.get("notified"):
                source = fact["add"].get("source", {})
                peer = (source.get("dm") or {}).get("ship") if isinstance(source, dict) else None
                if peer and any(tag in event for tag in ("dm-post", "dm-reply", "dm-invite", "dm-post-mention", "dm-reply-mention")):
                    token = peer + ":" + str(fact["add"].get("time") or event)
                    if token not in self.recent_notifications:
                        self.recent_notifications.append(token)
                        self.emit("notification", ship=peer, invite="dm-invite" in event)
                        self.pool.submit(self.refresh_unreads)
            return
        if "activity" in fact and isinstance(fact["activity"], dict):
            updates = dm_unreads(fact["activity"])
            self.unreads.update(updates)
            self.emit("unreads", counts=self.unreads, total=sum(self.unreads.values()))
            return
        if "read" in fact and isinstance(fact["read"], dict):
            read = fact["read"]
            peer = (read.get("source", {}).get("dm") or {}).get("ship")
            if peer:
                self.unreads[peer] = dm_unreads({"ship/" + peer: read.get("activity")}).get(peer, 0)
                self.emit("unreads", counts=self.unreads, total=sum(self.unreads.values()))
            return
        if "page" in fact or "peer" in fact:
            self.pool.submit(self.load_book)
        elif "net" in fact or "invited" in fact:
            self.pool.submit(self.refresh)

    def load_book(self) -> None:
        generation = self.generation
        book = self.scry("contacts", "/v1/book")
        with self.channel_lock:
            if generation != self.generation:
                return
            self.book = book
            self.contacts = merge_contacts(book, self.directory)
            self.emit("contacts", contacts=self.contacts)

    def load_directory(self) -> None:
        if self.directory_loaded:
            return
        with self.directory_lock:
            if self.directory_loaded:
                return
            generation = self.generation
            directory = self.scry("contacts", "/v1/directory")
            if generation != self.generation:
                return
            self.directory = directory
            self.directory_loaded = True
            self.contacts = merge_contacts(self.book, self.directory)
            self.emit("contacts", contacts=self.contacts)

    def effect_observation(self):
        with self.channel_lock:
            binding=(self.generation,self.base,self.ship)
            try:
                candidates=self.effects.candidates(self.base,self.ship) if self.ship else []
            except (OSError,ValueError):
                candidates=[]
            return binding,candidates

    def reconcile_effects(self,binding,candidates,peers=None,activity=None):
        with self.channel_lock:
            if binding!=(self.generation,self.base,self.ship):
                return
            try:
                self.effects.observe(binding[1],binding[2],candidates,peers,activity)
            except (OSError,ValueError):
                pass  # Keep authenticated inbox reads usable; report ledger health.
            self.emit_outbound()

    def refresh_unreads(self) -> None:
        binding,candidates=self.effect_observation()
        activity=self.scry("activity", "/v4/activity")
        counts=dm_unreads(activity)
        with self.channel_lock:
            if binding!=(self.generation,self.base,self.ship):
                return
            self.unreads = counts
            self.emit("unreads", counts=counts, total=sum(counts.values()))
            self.reconcile_effects(binding,candidates,activity=activity)

    def refresh(self, force: bool = False) -> None:
        if not self.ship:
            return
        with self.refresh_lock:
            if not force and time.monotonic() - self.last_refresh < 1:
                return
            binding,candidates=self.effect_observation()
            # Small parallel scries, not the heavyweight all-history init.
            peers_future = self.pool.submit(self.scry, "chat", "/dm")
            archived_future = self.pool.submit(self.scry, "chat", "/dm/archive")
            heads_future = self.pool.submit(self.scry, "groups-ui", "/v4/heads")
            book_future = self.pool.submit(self.scry, "contacts", "/v1/book")
            invites_future = self.pool.submit(self.scry, "chat", "/dm/invited")
            activity_future = self.pool.submit(self.scry, "activity", "/v4/activity")
            peers, heads, book, invites = (future.result() for future in
                                           (peers_future, heads_future, book_future, invites_future))
            archived = archived_future.result()
            activity=activity_future.result()
            counts = dm_unreads(activity)
            if binding!=(self.generation,self.base,self.ship):
                return
            self.book = book
            self.unreads = counts
            self.contacts = merge_contacts(book, self.directory)
            self.emit("contacts", contacts=self.contacts)
            self.emit("unreads", counts=counts, total=sum(counts.values()))
            rows = inbox_of(peers, heads, self.contacts, archived)
            self.reconcile_effects(binding,candidates,peers,activity)
            existing = {row["ship"] for row in rows}
            if isinstance(invites, list):
                rows.extend({"ship": peer, "name": self.contacts.get(peer) or peer,
                              "recency": 0, "preview": "DM invitation", "latestAuthor": "", "invite": True}
                            for peer in invites if isinstance(peer, str) and peer.startswith("~") and peer not in existing)
            self.emit("inbox", rows=rows)
            self.emit("apps", talon=talon_appimage() is not None)
            if force:
                self.emit("refreshed", count=len(rows))
            self.last_refresh = time.monotonic()

    def open_dm(self, ship: str, cursor: str | None) -> None:
        binding = (self.generation, self.base, self.ship)
        if not self.ship:
            raise ValueError("Sign in first")
        if cursor:
            digits = str(cursor).replace(".", "")
            if not digits.isdecimal() or len(digits) > 60:
                raise ValueError("Invalid history cursor")
            # JSON page cursors are plain decimal, but the %chat scry path
            # parses @ud, which requires three-digit dot grouping.
            dotted = f"{int(digits):,}".replace(",", ".")
        path = f"/v4/dm/{ship}/writs/older/{dotted}/20/light" if cursor else f"/v4/dm/{ship}/writs/newest/20/light"
        try:
            page = parse_page(self.scry("chat", path))
        except urllib.error.HTTPError as error:
            if error.code != 404 or cursor:
                raise
            page = {"messages": [], "older": None}  # Valid new recipient.
        with self.channel_lock:
            if binding != (self.generation, self.base, self.ship):
                return
            self.outbound.observe(binding[1], binding[2], ship, page["messages"])
        self.emit("history", ship=ship, append=bool(cursor), **page)
        if not cursor:
            self.pool.submit(self.mark_read, ship)

    def mark_read(self, ship: str) -> None:
        try:
            binding = self.acknowledged_effect(ship, "mark-read")
            with self.channel_lock:
                if binding != (self.generation, self.base, self.ship):
                    return
                self.unreads[ship] = 0
                self.emit("unreads", counts=self.unreads, total=sum(self.unreads.values()))
        except Exception:
            # This background read marker has no foreground request to complete.
            # Preserve the unknown intent and current unread count for refresh.
            pass
        finally:
            self.emit_outbound()

    def acknowledged_effect(self, ship: str, kind: str) -> tuple[int, str, str]:
        label = "Invitation" if kind == "accept-invitation" else "Read marker"
        with self.channel_lock:
            if self.closing:
                raise ValueError("DMs is stopping its connection")
            if not self.ship or not self.channel:
                raise ValueError("Sign in first")
            if self.channel_record is not None and self.channel_record["phase"] != "open":
                raise StoreError("Connection cleanup is pending; new actions are paused")
            if kind == "accept-invitation":
                payload = {"app": "chat", "mark": "chat-dm-rsvp", "json": {"ship": ship, "ok": True}}
            elif kind == "mark-read":
                payload = {"app": "activity", "mark": "activity-action", "json": {"read": {
                    "source": {"dm": {"ship": ship}}, "action": {"all": {"time": None, "deep": False}}}}}
            else:
                raise ValueError("Unsupported conversation action")
            binding = (self.generation, self.base, self.ship)
            self.request_id += 1
            ident = self.request_id
            ack = threading.Event()
            result: dict = {}
            self.pending[ident] = (ack, result)
            body = [{"id": ident, "action": "poke", "ship": self.ship[1:], **payload}]
            try:
                operation = self.effects.begin(self.base, self.ship, ship, kind)
                self.request("/~/channel/" + self.channel, json.dumps(body).encode(), "PUT")
            except Exception:
                self.pending.pop(ident, None)
                raise
        try:
            if not ack.wait(12):
                raise ValueError(label + " status unknown; refresh before retrying")
            if result.get("unknown"):
                raise ValueError(label + " status unknown (unrecognized ship acknowledgement); refresh before retrying")
            if result.get("error"):
                self.effects.finish(operation, "rejected")
                raise ValueError("Ship rejected the " + label.lower())
            self.effects.finish(operation, "acknowledged")
            return binding
        finally:
            self.pending.pop(ident, None)

    def accept(self, ship: str) -> None:
        binding = self.acknowledged_effect(ship, "accept-invitation")
        with self.channel_lock:
            if binding != (self.generation, self.base, self.ship):
                return
            self.emit("accepted", ship=ship)
        self.last_refresh = 0.0
        if not self.closing:
            self.refresh()

    def send(self, ship: str, text: str, request: object) -> None:
        if not text.strip() or len(text) > 10000:
            raise ValueError("Enter a message (at most 10,000 characters)")
        with self.channel_lock:
            if self.closing:
                raise ValueError("DMs is stopping its connection")
            if not self.ship or not self.channel:
                raise ValueError("Sign in first")
            if self.channel_record is not None and self.channel_record["phase"] != "open":
                raise StoreError("Connection cleanup is pending; new actions are paused")
            sent = int(time.time() * 1000)
            ident = post_id(self.ship, sent)
            message = {"id": ident, "author": self.ship, "sent": sent, "text": text}
            payload = {"ship": ship, "diff": {"id": ident, "delta": {"add": {
                "essay": {"content": [{"inline": [text]}], "sent": sent,
                          "author": self.ship, "kind": "/chat", "meta": None, "blob": None},
                "time": None}}}}
            # The local receipt may arrive before HTTP PUT returns.
            self.request_id += 1
            ident_num = self.request_id
            ack = threading.Event()
            result: dict = {}
            self.pending[ident_num] = (ack, result)
            body = [{"id": ident_num, "action": "poke", "ship": self.ship[1:],
                     "app": "chat", "mark": "chat-dm-action-2", "json": payload}]
            try:
                # A crash after this write is conservatively an uncertain send.
                operation = self.outbound.begin(self.base, self.ship, ship, message)
                self.request("/~/channel/" + self.channel, json.dumps(body).encode(), "PUT")
            except Exception:
                self.pending.pop(ident_num, None)
                raise
        try:
            if not ack.wait(12):
                raise ValueError("Send status unknown (no ship acknowledgement). Check the conversation before retrying")
            if result.get("unknown"):
                raise ValueError("Send status unknown (unrecognized ship acknowledgement). Check the conversation before retrying")
            if result.get("error"):
                self.outbound.finish(operation, "rejected")
                raise ValueError("Ship rejected the message")
            self.outbound.finish(operation, "acknowledged")
            self.emit("sent", request=request, ship=ship, message=message)
            if not self.closing:
                self.pool.submit(self.refresh)
        finally:
            self.pending.pop(ident_num, None)


    def stop_transport(self):
        """Main-thread-only drain. Success is transport closure, not collection readiness.

        Keep sessions and unresolved sends. Failed deletion stays retryable with
        this exact channel; do not clear its binding or enable replacements.
        """
        self.closing = True
        self.pool.shutdown(wait=True, cancel_futures=False)
        with self.channel_lock:
            if self.pending:
                return {"closed": False, "reason": "pending-receipts"}
            if self.channel:
                try:
                    self.close_channel()
                except (OSError, urllib.error.URLError, ValueError):
                    return {"closed": False, "reason": "channel-cleanup-unknown"}
                self.generation += 1
            self.cancel_streams()
        deadline = time.monotonic() + 5
        for listener in self.listeners:
            listener.join(timeout=max(0, deadline-time.monotonic()))
        if any(listener.is_alive() for listener in self.listeners):
            return {"closed": False, "reason": "stream-still-open"}
        try:
            saved = self.transport.status()
            if saved is not None and any(row["phase"] != "closed" for row in saved["channels"]):
                if self.transport_lease is None or self.transport_session is None:
                    return {"closed": False, "reason": "channel-cleanup-unknown"}
                for row in saved["channels"]:
                    if row["phase"] != "closed":
                        self.delete_transport_record(row)
        except (OSError, urllib.error.URLError, ValueError):
            return {"closed": False, "reason": "channel-cleanup-unknown"}
        try:
            unresolved = self.outbound.unresolved()
            effects = self.effects.unresolved()
        except (OSError, ValueError):
            return {"closed": False, "reason": "outbound-history-unavailable"}
        self.release_transport_owner()
        return {"closed": True, "unresolvedSends": unresolved, "unresolvedEffects": effects, "collectionReady": False}


def main() -> int:
    bridge = Bridge()
    closed = False
    try:
        bridge.emit("ready")
        bridge.emit_outbound()
        bridge.emit("apps", talon=talon_appimage() is not None)
        bridge.pool.submit(bridge.command, {"action": "restore"})
        for line in sys.stdin:
            try:
                command = json.loads(line)
                if isinstance(command, dict):
                    if command.get("action") == "shutdown-transport":
                        result = bridge.stop_transport()
                        closed = result["closed"]
                        bridge.emit("transport-state", **result)
                        if closed:
                            break
                    elif bridge.closing:
                        bridge.command(command)
                    else:
                        bridge.pool.submit(bridge.command, command)
            except (ValueError, TypeError):
                bridge.emit("error", message="Invalid command")
    finally:
        # The shell can vanish without sending shutdown-transport. EOF and a
        # broken output pipe still require draining workers and deleting the
        # exact owned channel. There is no receiver for a final stdout receipt.
        if not closed:
            closed = bridge.stop_transport()["closed"]
    if not closed:
        print("DMs connection cleanup remains unconfirmed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
