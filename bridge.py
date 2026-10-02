#!/usr/bin/env python3
"""Private JSON-lines bridge between Quickshell and a running %groups desk.

One process owns the live cookie and Eyre channel. The session cookie survives
shell restarts in Secret Service; the +code is never saved or logged.
"""

from __future__ import annotations

import concurrent.futures
import collections
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import select
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from session_store import SessionStore, StoreError, cookie_for, origin
from external_apps import launch_talon, talon_appimage
from launcher_entry import install_launcher, remove_launcher

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
    def __init__(self, session_store=None) -> None:
        self.output_lock = threading.Lock()
        self.auth_lock = threading.Lock()
        self.channel_lock = threading.Lock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=10)
        self.refresh_lock = threading.Lock()
        self.directory_lock = threading.Lock()
        self.last_refresh = 0.0
        self.directory_loaded = False
        self.activity_subscription = 0
        self.recent_notifications: collections.deque[str] = collections.deque(maxlen=256)
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.opener.addheaders = [("User-Agent", USER_AGENT)]
        self.base = ""
        self.ship = ""
        self.channel = ""
        self.generation = 0
        self.request_id = 0
        self.pending: dict[int, tuple[threading.Event, dict]] = {}
        self.contacts: dict[str, str] = {}
        self.unreads: dict[str, int] = {}
        self.book: dict = {}
        self.directory: dict = {}
        self.session_store = session_store if session_store is not None else SessionStore()
        self.manage_launcher = session_store is None

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
            if action == "login":
                with self.auth_lock:
                    result = self.login(payload.get("url", ""), payload.get("code", ""))
                self.emit("login", request=request_id, ship=result)
                self.pool.submit(self.refresh)
            elif action == "restore":
                with self.auth_lock:
                    result = self.restore() if not self.ship else None
                if result:
                    self.emit("login", ship=result, restored=True)
                    self.pool.submit(self.refresh)
            elif action == "logout":
                with self.auth_lock:
                    self.logout()
                self.emit("logout", request=request_id)
            elif action == "refresh":
                self.refresh(force=True)
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

    def reset_runtime(self) -> None:
        self.generation += 1
        if self.channel:
            try:
                self.channel_action({"action": "delete"})
            except (OSError, urllib.error.URLError):
                pass
        self.base = self.ship = self.channel = ""
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

    def logout(self) -> None:
        # A locked keyring must not pretend to sign out: the saved session
        # would come back at the next shell restart. Keep the UI retryable.
        self.session_store.forget()
        self.reset_runtime()
        if self.manage_launcher:
            try:
                remove_launcher()
            except OSError:
                pass  # The credential is already gone; never undo sign-out.

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
        self.channel = f"urbit-dms-{secrets.token_hex(12)}"
        generation = self.generation
        threading.Thread(target=self.listen, args=(generation,), daemon=True).start()

    def restore(self) -> str | None:
        account = self.session_store.account()
        if account is None:
            return None
        session = self.session_store.lookup(account["url"], account["ship"])
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
        self.sync_launcher()
        self.start_session()
        return self.ship

    def login(self, url: str, code: str) -> str:
        self.reset_runtime()
        url = origin(url)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Enter a ship URL without a path or credentials")
        if not code.strip().removeprefix("+"):
            raise ValueError("Enter the code returned by +code")
        self.base = url
        body = urllib.parse.urlencode({"password": code.strip().removeprefix("+")}).encode()
        req = urllib.request.Request(url + "/~/login", data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self.opener.open(req, timeout=25) as response:
                response.read()
        except urllib.error.HTTPError as error:
            raise ValueError(f"Login rejected (HTTP {error.code}). Check the access key and ship URL") from None
        cookie = next((c for c in self.cookies if c.name.startswith("urbauth-~") and c.domain == parsed.hostname), None)
        if not cookie:
            raise ValueError("Login failed: no ship session cookie was returned")
        self.ship = cookie.name.removeprefix("urbauth-")
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
        session = {"url": url, "ship": self.ship, "cookieName": cookie.name, "cookieValue": cookie.value}
        try:
            self.session_store.save(session)
        except StoreError:
            self.reset_runtime()
            raise
        self.sync_launcher()
        self.start_session()
        return self.ship

    def channel_action(self, fields: dict) -> int:
        if not self.channel:
            raise ValueError("Sign in first")
        with self.channel_lock:
            self.request_id += 1
            request_id = self.request_id
            body = [{"id": request_id, **fields}]
            self.request("/~/channel/" + self.channel, json.dumps(body).encode(), "PUT")
            return request_id

    def listen(self, generation: int) -> None:
        pause = 1
        while generation == self.generation and self.channel:
            try:
                url = self.base + "/~/channel/" + self.channel
                req = urllib.request.Request(url, headers={"Accept": "text/event-stream", "Accept-Encoding": "identity"})
                # PUT first creates the channel; GET then opens its SSE stream.
                self.channel_action({"action": "subscribe", "ship": self.ship[1:], "app": "chat", "path": "/v4"})
                self.channel_action({"action": "subscribe", "ship": self.ship[1:], "app": "contacts", "path": "/v1/news"})
                self.activity_subscription = self.channel_action({"action": "subscribe", "ship": self.ship[1:], "app": "activity", "path": "/v5"})
                self.channel_action({"action": "subscribe", "ship": self.ship[1:], "app": "chat", "path": "/dm/invited"})
                with self.opener.open(req, timeout=65) as response:
                    if generation != self.generation:
                        return
                    pause = 1
                    frame: list[str] = []
                    event_id = None
                    for raw in response:
                        if generation != self.generation:
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
            if generation != self.generation:
                break
            time.sleep(pause)
            pause = min(pause * 2, 30)
            # Do not leave the old subscribed channel clogging Eyre.
            try:
                self.channel_action({"action": "delete"})
            except (OSError, urllib.error.URLError, ValueError):
                pass
            if generation != self.generation:
                break
            # A new channel prevents duplicate subscriptions and stale facts.
            self.channel = f"urbit-dms-{secrets.token_hex(12)}"
            try:
                self.refresh()
            except (OSError, urllib.error.URLError, ValueError):
                pass

    def on_fact(self, outer: dict, event_id: str | None) -> None:
        if not isinstance(outer, dict):
            return
        if event_id and event_id.isdecimal() and outer.get("response") == "diff":
            self.pool.submit(self.channel_action, {"action": "ack", "event-id": int(event_id)})
        if outer.get("response") == "poke":
            pending = self.pending.get(outer.get("id"))
            if pending:
                pending[1]["error"] = outer.get("err")
                pending[0].set()
            return
        if outer.get("response") == "subscribe" and outer.get("id") == self.activity_subscription and outer.get("err"):
            self.pool.submit(self.channel_action, {"action": "subscribe", "ship": self.ship[1:],
                                                   "app": "activity", "path": "/v4"})
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
        self.book = self.scry("contacts", "/v1/book")
        if generation != self.generation:
            return
        self.contacts = merge_contacts(self.book, self.directory)
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

    def refresh_unreads(self) -> None:
        generation = self.generation
        counts = dm_unreads(self.scry("activity", "/v4/activity"))
        if generation != self.generation:
            return
        self.unreads = counts
        self.emit("unreads", counts=counts, total=sum(counts.values()))

    def refresh(self, force: bool = False) -> None:
        if not self.ship:
            return
        with self.refresh_lock:
            if not force and time.monotonic() - self.last_refresh < 1:
                return
            generation = self.generation
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
            counts = dm_unreads(activity_future.result())
            if generation != self.generation:
                return
            self.book = book
            self.unreads = counts
            self.contacts = merge_contacts(book, self.directory)
            self.emit("contacts", contacts=self.contacts)
            self.emit("unreads", counts=counts, total=sum(counts.values()))
            rows = inbox_of(peers, heads, self.contacts, archived)
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
        self.emit("history", ship=ship, append=bool(cursor), **page)
        if not cursor:
            self.pool.submit(self.mark_read, ship)

    def mark_read(self, ship: str) -> None:
        self.channel_action({"action": "poke", "ship": self.ship[1:], "app": "activity",
                             "mark": "activity-action", "json": {"read": {"source": {"dm": {"ship": ship}},
                             "action": {"all": {"time": None, "deep": False}}}}})
        self.unreads[ship] = 0
        self.emit("unreads", counts=self.unreads, total=sum(self.unreads.values()))

    def accept(self, ship: str) -> None:
        with self.channel_lock:
            self.request_id += 1
            ident = self.request_id
            ack = threading.Event()
            result: dict = {}
            self.pending[ident] = (ack, result)
            body = [{"id": ident, "action": "poke", "ship": self.ship[1:], "app": "chat",
                     "mark": "chat-dm-rsvp", "json": {"ship": ship, "ok": True}}]
            try:
                self.request("/~/channel/" + self.channel, json.dumps(body).encode(), "PUT")
            except Exception:
                self.pending.pop(ident, None)
                raise
        try:
            if not ack.wait(12):
                raise ValueError("Invitation status unknown; refresh before retrying")
            if result.get("error"):
                raise ValueError("Ship rejected the invitation response")
            self.emit("accepted", ship=ship)
            self.last_refresh = 0.0
            self.refresh()
        finally:
            self.pending.pop(ident, None)

    def send(self, ship: str, text: str, request: object) -> None:
        if not self.ship:
            raise ValueError("Sign in first")
        if not text.strip() or len(text) > 10000:
            raise ValueError("Enter a message (at most 10,000 characters)")
        sent = int(time.time() * 1000)
        ident = post_id(self.ship, sent)
        payload = {"ship": ship, "diff": {"id": ident, "delta": {"add": {
            "essay": {"content": [{"inline": [text]}], "sent": sent,
                      "author": self.ship, "kind": "/chat", "meta": None, "blob": None},
            "time": None}}}}
        # Register before PUT: on a local ship the ack can precede its return.
        with self.channel_lock:
            self.request_id += 1
            ident_num = self.request_id
            ack = threading.Event()
            result: dict = {}
            self.pending[ident_num] = (ack, result)
            body = [{"id": ident_num, "action": "poke", "ship": self.ship[1:],
                     "app": "chat", "mark": "chat-dm-action-2", "json": payload}]
            try:
                self.request("/~/channel/" + self.channel, json.dumps(body).encode(), "PUT")
            except Exception:
                self.pending.pop(ident_num, None)
                raise
        try:
            if not ack.wait(12):
                raise ValueError("Send status unknown (no ship acknowledgement). Check the conversation before retrying")
            if result.get("error"):
                raise ValueError("Ship rejected the message")
            self.emit("sent", request=request, ship=ship,
                      message={"id": ident, "author": self.ship, "sent": sent, "text": text})
            self.pool.submit(self.refresh)
        finally:
            self.pending.pop(ident_num, None)


def main() -> None:
    bridge = Bridge()
    bridge.emit("ready")
    bridge.emit("apps", talon=talon_appimage() is not None)
    bridge.pool.submit(bridge.command, {"action": "restore"})
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if isinstance(command, dict):
                bridge.pool.submit(bridge.command, command)
        except (ValueError, TypeError):
            bridge.emit("error", message="Invalid command")


if __name__ == "__main__":
    main()
