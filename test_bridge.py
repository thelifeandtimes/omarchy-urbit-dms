import json
import tempfile
from outbound_journal import OutboundJournal
import io
import http.cookiejar
from concurrent.futures import Future
import unittest
import urllib.error
from unittest.mock import patch

from bridge import Bridge, USER_AGENT, dm_unreads, inbox_of, merge_contacts, message_of, parse_page, patp, post_id, text_of
from session_store import StoreError


class MemoryStore:
    def __init__(self, session=None):
        self.session = session
        self.error = None

    def account(self):
        return {"version": 1, "url": self.session["url"], "ship": self.session["ship"]} if self.session else None

    def lookup(self, url, ship, check_expiry=True):
        if self.error:
            raise self.error
        return self.session

    def save(self, session):
        if self.error:
            raise self.error
        self.session = session

    def forget(self):
        if self.error:
            raise self.error
        self.session = None


class WireShapeTests(unittest.TestCase):
    def test_dm_unreads_exclude_thread_and_channel_counts(self):
        activity = {
            "ship/~bus": {"count": 12, "unread": {"count": 2}},
            "ship/~zod": {"unread": None},
            "dm-thread/~bus/~bus/1": {"unread": {"count": 8}},
            "channel/chat/~bus/main": {"unread": {"count": 9}},
        }
        self.assertEqual(dm_unreads(activity), {"~bus": 2})

    def test_da_id_matches_talon_fixture(self):
        self.assertEqual(post_id("~zod", 0), "~zod/170.141.184.475.152.167.957.503.069.145.530.368.000")
        self.assertEqual(post_id("~bot-test", 1701275662689).split("/")[0], "~bot-test")

    def test_contacts_prefer_local_override(self):
        book = {"~bus": [{"nickname": {"value": "remote"}}, {"nickname": {"value": "my friend"}}]}
        directory = {"~bus": {"contact": {"nickname": {"value": "old"}}, "mod": {}}}
        self.assertEqual(merge_contacts(book, directory)["~bus"], "my friend")

    def test_contact_label_with_patp_keeps_only_nickname(self):
        book = {"~bus": [{"nickname": {"value": ""}}, {"nickname": {"value": "~bus (kenny)"}}]}
        self.assertEqual(merge_contacts(book, {})["~bus"], "kenny")

    def test_inbox_merges_empty_threads_and_sorts_recency(self):
        writ = {"type": "writ", "seal": {"id": "~bus/123"},
                "essay": {"author": "~bus", "sent": 200, "content": [{"inline": ["hi"]}]}}
        heads = [{"whom": "~bus", "recency": 300, "latest": writ}]
        rows = inbox_of(["~zod", "~bus"], {"channels": [], "dms": heads}, {"~bus": "Pal"})
        self.assertEqual([r["ship"] for r in rows], ["~bus", "~zod"])
        self.assertEqual(rows[0]["preview"], "hi")
        self.assertEqual(rows[0]["latestAuthor"], "~bus")

    def test_inbox_includes_all_accepted_and_archived_conversations(self):
        peers = ["~peer" + chr(97 + i // 26) + chr(97 + i % 26) for i in range(201)]
        rows = inbox_of(peers, {"channels": [], "dms": []}, {}, ["~archived", peers[0]])
        self.assertEqual(len(rows), 202)
        self.assertEqual(len({row["ship"] for row in rows}), 202)
        archived = next(row for row in rows if row["ship"] == "~archived")
        self.assertTrue(archived["archived"])
        self.assertEqual(archived["preview"], "Archived conversation")

    def test_page_and_rich_message_fallback(self):
        writ = {"type": "writ", "essay": {"author": "~bus", "sent": 2,
                "content": [{"inline": ["Hello", {"bold": [" world"]}]}, {"block": {"image": "https://x"}}]}}
        page = parse_page({"writs": {"42": writ, "41": {"type": "tombstone"}}, "older": "40"})
        self.assertEqual(page["messages"][0]["text"], "Hello world\n[attachment]")
        self.assertEqual(page["older"], "40")

    def test_invitation_acceptance_notice_is_not_a_self_authored_chat_message(self):
        notice = {"type": "post", "essay": {
            "author": "~zod", "sent": 42, "kind": "/chat/notice",
            "content": [{"inline": [{"ship": "~rus"}, " joined the chat"]}]}}
        parsed = message_of(notice)
        self.assertEqual(parsed["text"], "~rus joined the chat")
        self.assertEqual(parsed["kind"], "/chat/notice")
        self.assertEqual(parsed["acceptedShip"], "~rus")
        ordinary = {"essay": {"author": "~zod", "kind": "/chat",
                              "content": [{"inline": ["~rus joined the chat"]}]}}
        self.assertEqual(message_of(ordinary)["acceptedShip"], "")
        other_notice = {"essay": {"author": "~zod", "kind": "/chat/notice",
                                  "content": [{"inline": [{"ship": "~rus"}, " archived the channel"]}]}}
        self.assertEqual(message_of(other_notice)["acceptedShip"], "")

        rows = inbox_of(["~rus"], {"dms": [{"whom": "~rus", "latest": notice}]}, {})
        self.assertEqual(rows[0]["latestKind"], "/chat/notice")

        bridge = Bridge()
        events = []
        bridge.emit = lambda event, **fields: events.append((event, fields))
        bridge.on_fact({"response": "diff", "json": {"whom": "~rus", "id": "notice/42",
                                                "response": {"add": {"essay": notice["essay"]}}}}, None)
        self.assertEqual(events[0][0], "message")
        self.assertEqual(events[0][1]["message"]["acceptedShip"], "~rus")

    def test_patp_input(self):
        self.assertEqual(patp(" ZOD "), "~zod")
        for invalid in ("", "http://host", "~bus/evil", "~123"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                patp(invalid)


class SendTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.state_patch=patch.dict("os.environ",{"XDG_STATE_HOME":self.temporary.name})
        self.state_patch.start();self.addCleanup(self.state_patch.stop)

    def test_browser_handoff_rejects_expired_session_before_starting_browser(self):
        session = {"url": "https://ship.example", "ship": "~zod",
                   "cookieName": "urbauth-~zod", "cookieValue": "expired"}
        bridge = Bridge(session_store=MemoryStore(session))
        bridge.base, bridge.ship = session["url"], session["ship"]
        denied = urllib.error.HTTPError(session["url"], 403, "Forbidden", None, None)
        bridge.scry = lambda *args: (_ for _ in ()).throw(denied)
        try:
            with patch("bridge.subprocess.Popen") as spawn, self.assertRaisesRegex(StoreError, "expired"):
                bridge.open_browser()
            spawn.assert_not_called()
        finally:
            denied.close()

    def test_older_history_cursor_uses_urbit_dotted_decimal(self):
        bridge = Bridge()
        bridge.ship = "~zod"
        paths = []
        events = []
        bridge.scry = lambda app, path: paths.append(path) or {"writs": {}, "older": None}
        bridge.emit = lambda event, **data: events.append((event, data))
        bridge.open_dm("~bus", "170141184508164433542163182225249533952")
        self.assertEqual(paths, ["/v4/dm/~bus/writs/older/170.141.184.508.164.433.542.163.182.225.249.533.952/20/light"])
        self.assertEqual(events[0][0], "history")
        with self.assertRaisesRegex(ValueError, "Invalid history cursor"):
            bridge.open_dm("~bus", "../etc")

    def test_force_refresh_bypasses_throttle_and_emits_rows(self):
        bridge = Bridge()
        bridge.ship = "~zod"
        emitted = []
        bridge.emit = lambda name, **fields: emitted.append((name, fields))
        results = {
            ("chat", "/dm"): ["~bus"],
            ("chat", "/dm/archive"): [],
            ("groups-ui", "/v4/heads"): {"channels": [], "dms": []},
            ("contacts", "/v1/book"): {},
            ("chat", "/dm/invited"): [],
            ("activity", "/v4/activity"): {"ship/~bus": {"unread": {"count": 2}}},
        }
        bridge.scry = lambda agent, path: results[agent, path]

        def completed(func, *args):
            future = Future()
            future.set_result(func(*args))
            return future

        bridge.pool.submit = completed
        bridge.refresh()
        bridge.refresh()  # normal event burst is throttled
        bridge.refresh(force=True)  # user clicking refresh is never throttled
        self.assertEqual(len([name for name, _ in emitted if name == "inbox"]), 2)
        self.assertEqual([fields["total"] for name, fields in emitted if name == "unreads"], [2, 2])
        self.assertEqual(len([name for name, _ in emitted if name == "refreshed"]), 1)

    def test_refresh_uses_groups_ui_dm_heads_for_previews(self):
        bridge = Bridge()
        bridge.ship = "~zod"
        emitted = []
        bridge.emit = lambda name, **fields: emitted.append((name, fields))
        writ = {"type": "writ", "essay": {"author": "~bus", "sent": 1700000000000,
                "content": [{"inline": ["New message"]}]}}
        results = {
            ("chat", "/dm"): ["~bus", "~nec"],
            ("chat", "/dm/archive"): [],
            ("groups-ui", "/v4/heads"): {"channels": [], "dms": [
                {"whom": "~bus", "recency": 1700000000000, "latest": writ}]},
            ("contacts", "/v1/book"): {},
            ("chat", "/dm/invited"): [],
            ("activity", "/v4/activity"): {},
        }

        def scry(agent, path):
            return results[agent, path]

        bridge.scry = scry
        bridge.refresh(force=True)
        rows = next(fields["rows"] for name, fields in emitted if name == "inbox")
        self.assertEqual([row["ship"] for row in rows], ["~bus", "~nec"])
        self.assertEqual(rows[0]["preview"], "New message")

    def test_login_posts_code_as_form_and_keeps_cookie_in_memory(self):
        store = MemoryStore()
        bridge = Bridge(session_store=store)
        self.assertIn(("User-Agent", USER_AGENT), bridge.opener.addheaders)
        traffic = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()

        class FakeOpener:
            def open(self, req, timeout):
                traffic.append((req.full_url, req.data))
                if req.full_url.endswith("/~/login"):
                    bridge.cookies.set_cookie(http.cookiejar.Cookie(
                        version=0, name="urbauth-~zod", value="secret-cookie",
                        port=None, port_specified=False, domain="localhost",
                        domain_specified=False, domain_initial_dot=False,
                        path="/", path_specified=True, secure=False, expires=None,
                        discard=True, comment=None, comment_url=None, rest={}, rfc2109=False))
                    return Response(b"")
                return Response(b"[\"~bus\"]")

        bridge.opener = FakeOpener()
        bridge.listen = lambda generation: None
        self.addCleanup(bridge.release_transport_owner)
        self.addCleanup(bridge.pool.shutdown, wait=True)
        self.assertEqual(bridge.login("http://localhost:8080", "+sample-code"), "~zod")
        self.assertEqual(traffic[0], ("http://localhost:8080/~/login", b"password=sample-code"))
        self.assertEqual(traffic[1][0], "http://localhost:8080/~/scry/chat/dm.json")
        self.assertEqual(store.session["cookieValue"], "secret-cookie")
        self.assertNotIn("sample-code", str(store.session))

    def test_restore_uses_saved_origin_cookie_and_renews_live_channel(self):
        session = {"url": "https://ship.example", "ship": "~zod",
                   "cookieName": "urbauth-~zod", "cookieValue": "secret-cookie"}
        bridge = Bridge(session_store=MemoryStore(session))
        bridge.listen = lambda generation: None
        self.addCleanup(bridge.release_transport_owner)
        self.addCleanup(bridge.pool.shutdown, wait=True)
        traffic = []
        bridge.scry = lambda agent, path: traffic.append((agent, path)) or ["~bus"]
        self.assertEqual(bridge.restore(), "~zod")
        self.assertEqual(traffic, [("chat", "/dm")])
        self.assertTrue(bridge.channel.startswith("urbit-dms-"))
        self.assertEqual([(c.name, c.value, c.domain, c.secure) for c in bridge.cookies],
                         [("urbauth-~zod", "secret-cookie", "ship.example", True)])

    def test_running_bridge_refresh_adopts_new_account_once(self):
        store = MemoryStore()
        bridge = Bridge(session_store=store)
        events, starts, refreshes, scries = [], [], [], []
        bridge.emit = lambda event, **data: events.append((event, data))
        bridge.start_session = lambda: starts.append(bridge.ship)
        bridge.refresh = lambda force=False: refreshes.append(force)
        bridge.scry = lambda agent, path: scries.append((agent, path)) or []
        try:
            bridge.command({"action": "restore"})
            self.assertEqual(bridge.ship, "")
            self.assertEqual(events, [])
            store.save({"url": "https://ship.example", "ship": "~zod",
                        "cookieName": "urbauth-~zod", "cookieValue": "new-approved-session"})
            bridge.command({"action": "refresh"})
            self.assertEqual(bridge.ship, "~zod")
            self.assertEqual(scries, [("chat", "/dm")])
            self.assertEqual(starts, ["~zod"])
            self.assertEqual(events, [("login", {"ship": "~zod", "restored": True})])
            # A revoked/replaced account clears the old client and presents login;
            # this refresh must not silently adopt the replacement account.
            store.save({"url": "https://other.example", "ship": "~bus",
                        "cookieName": "urbauth-~bus", "cookieValue": "different-session"})
            bridge.command({"action": "refresh"})
            self.assertEqual((bridge.ship, bridge.base), ("", ""))
            self.assertEqual(len(starts), 1)
            self.assertEqual(refreshes, [True])
            self.assertIn(("logout", {"revoked": True}), events)
            self.assertNotIn("new-approved-session", str(events))
        finally:
            bridge.pool.shutdown(wait=True, cancel_futures=True)

    def test_refresh_does_not_fall_back_when_saved_account_is_locked(self):
        store = MemoryStore({"url": "https://ship.example", "ship": "~zod"})
        store.error = StoreError("Unlock keyring")
        bridge = Bridge(session_store=store)
        events = []
        bridge.emit = lambda event, **data: events.append((event, data))
        bridge.scry = lambda *_args: self.fail("locked credential reached a ship")
        try:
            bridge.command({"action": "refresh", "request": 1})
            self.assertEqual(bridge.ship, "")
            self.assertEqual(events, [("error", {"request": 1, "action": "refresh", "message": "Unlock keyring"})])
            self.assertIsNotNone(store.account())
        finally:
            bridge.pool.shutdown(wait=True, cancel_futures=True)

    def test_expired_saved_cookie_does_not_sign_in(self):
        session = {"url": "https://ship.example", "ship": "~zod",
                   "cookieName": "urbauth-~zod", "cookieValue": "expired"}
        bridge = Bridge(session_store=MemoryStore(session))
        forbidden_error = urllib.error.HTTPError("https://ship.example", 403, "Forbidden", None, None)

        def forbidden(*args):
            raise forbidden_error

        bridge.scry = forbidden
        try:
            with self.assertRaisesRegex(StoreError, "expired"):
                bridge.restore()
        finally:
            forbidden_error.close()
        self.assertEqual(bridge.ship, "")
        self.assertEqual(list(bridge.cookies), [])

    def test_sign_out_does_not_claim_success_if_keyring_locked(self):
        store = MemoryStore({"url": "https://ship.example", "ship": "~zod"})
        store.error = StoreError("Unlock keyring")
        bridge = Bridge(session_store=store)
        bridge.ship = "~zod"
        with self.assertRaisesRegex(StoreError, "Unlock keyring"):
            bridge.logout()
        self.assertEqual(bridge.ship, "~zod")

    def test_send_poke_and_ack(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        bridge = Bridge(outbound_journal=OutboundJournal(temporary.name))
        bridge.base = "http://127.0.0.1:18081"
        bridge.ship = "~zod"
        bridge.channel = "test"
        sent = []
        events = []
        bridge.emit = lambda name, **fields: events.append({"event": name, **fields})

        def request(path, body, method):
            sent.extend(json.loads(body))
            # A local ship can ack before HTTP PUT returns.
            bridge.on_fact({"id": sent[-1]["id"], "response": "poke", "ok": "ok"}, None)

        bridge.request = request
        bridge.pool.submit = lambda func, *args: None
        bridge.send("~bus", "hey", 7)
        action = sent[0]
        self.assertEqual((action["app"], action["mark"], action["ship"]),
                         ("chat", "chat-dm-action-2", "zod"))
        self.assertEqual(action["json"]["ship"], "~bus")
        self.assertEqual(action["json"]["diff"]["delta"]["add"]["essay"]["content"],
                         [{"inline": ["hey"]}])
        self.assertTrue(any(event["event"] == "sent" and event["request"] == 7 for event in events))

    def test_activity_notifications_respect_notified(self):
        bridge = Bridge()
        events = []
        bridge.emit = lambda name, **fields: events.append((name, fields))
        fact = {"response": "diff", "json": {"add": {"source": {"dm": {"ship": "~bus"}},
                    "event": {"notified": True, "dm-post": {"key": {"id": "~bus/1"}}}}}}
        bridge.on_fact(fact, None)
        self.assertEqual(events, [("notification", {"ship": "~bus", "invite": False})])
        bridge.on_fact(fact, None)
        self.assertEqual(len(events), 1)
        fact["json"]["add"]["event"]["notified"] = False
        bridge.on_fact(fact, None)
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
