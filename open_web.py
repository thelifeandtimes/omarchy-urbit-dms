#!/usr/bin/env python3
"""Desktop-launcher entry point for the saved DM account."""

import subprocess

from bridge import Bridge
from session_store import SessionStore, StoreError


def main():
    try:
        store = SessionStore()
        account = store.account()
        if not account:
            raise StoreError("Sign in to Urbit DMs before opening Tlon Web")
        bridge = Bridge(store)
        bridge.base, bridge.ship = account["url"], account["ship"]
        bridge.open_browser()
    except (ValueError, OSError) as error:
        try:
            subprocess.Popen(["notify-send", "Tlon Web", str(error)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError:
            pass


if __name__ == "__main__":
    main()
