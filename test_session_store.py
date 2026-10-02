import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

from session_store import SessionStore, StoreError, cookie_for, origin


SESSION = {"url": "https://ship.example", "ship": "~zod",
           "cookieName": "urbauth-~zod", "cookieValue": "private-cookie"}


class SessionStoreTests(unittest.TestCase):
    def test_session_is_only_in_keyring_and_account_file_is_private(self):
        calls = []
        secrets = {}

        def runner(args, *, input, stdout, stderr, timeout, check):
            calls.append((args, input))
            url = args[-1]
            if args[1] == "store":
                secrets[url] = input
                return subprocess.CompletedProcess(args, 0, b"")
            if args[1] == "lookup":
                return subprocess.CompletedProcess(args, 0, secrets[url])
            if args[1] == "clear":
                del secrets[url]
                return subprocess.CompletedProcess(args, 0, b"")
            raise AssertionError(args)

        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "accounts", runner)
            store.save(SESSION)
            path = store.root / "account.json"
            raw = path.read_bytes()
            self.assertNotIn(b"private-cookie", raw)
            self.assertEqual(json.loads(raw), {"version": 1, "url": SESSION["url"], "ship": "~zod"})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(store.root.stat().st_mode), 0o700)
            self.assertEqual(store.account()["ship"], "~zod")
            self.assertEqual(store.lookup(SESSION["url"], "~zod"), SESSION)
            self.assertNotIn("private-cookie", str([args for args, _ in calls]))
            self.assertEqual(calls[0][1], json.dumps(SESSION).encode())
            self.assertEqual(calls[0][0][-4:], ["application", "omarchy-urbit-dms", "origin", SESSION["url"]])
            store.forget()
            self.assertIsNone(store.account())
            self.assertEqual(secrets, {})

    def test_locked_keyring_does_not_write_metadata(self):
        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, b"")

        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "accounts", runner)
            with self.assertRaisesRegex(StoreError, "Secret Service"):
                store.save(SESSION)
            self.assertIsNone(store.account())

    def test_invalid_account_file_or_cookie_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "accounts"
            root.mkdir()
            (root / "account.json").write_text('{"version":1,"url":"https://wrong.example/path","ship":"~zod"}')
            with self.assertRaises(StoreError):
                SessionStore(root).account()
        with self.assertRaises(StoreError):
            cookie_for({**SESSION, "url": "https://other.example/path"})
        with self.assertRaises(StoreError):
            cookie_for({**SESSION, "cookieValue": "a;other=secret"})
        self.assertEqual(origin("ship.example/"), "https://ship.example")


if __name__ == "__main__":
    unittest.main()
