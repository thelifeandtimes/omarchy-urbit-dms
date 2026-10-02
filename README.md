# Urbit DMs for Omarchy

A small Omarchy bar widget and overlay for 1:1 direct messages on a running ship with
Tlon's `%groups` desk. Requires Python 3, `secret-tool` with an unlocked Secret
Service provider (GNOME Keyring on Omarchy), and `notify-send` for desktop alerts.
The supported desk exposes `%chat /v4`, `%groups-ui /v4/heads`, `%contacts /v1`, and `%activity /v5`
(or `/v4`); older desks may need updating.

## Use

- **Super+Alt+U:** quick DM — fuzzy-search a nickname or `~ship`, select a
  recipient with Enter or a click, then write and send your message.
- **Hyper+U** (Super+Shift+Ctrl+Alt+U): inbox, contact search, conversation and history.
- Click the chat bubble in the bar to see the five most recent conversations;
  its center shows `~` for no unread DMs, 1–9 for that count, or `+` for 10+.
  Use the ↩ button on a conversation to reply directly beneath that message.
  The popup and inbox switch between nicknames (the default) and `~ship` IDs.
  When selecting or replying, the other identifier appears inline in
  parentheses so you can verify the delivery address before sending.
- **Open in → Tlon Web** opens `<ship URL>/apps/groups` in an authenticated
  off-the-record window of your default Chromium-based browser. The DM
  session cookie passes through local process pipes, never through a URL or
  command line, and is not added to the browser's normal profile. Closing
  the window ends this private browser session. Other default browsers do
  not currently support this handoff.
- **Open in → Talon** appears when an executable Talon AppImage is installed
  via `Talon.desktop`, `~/apps`, or `~/Applications`. It focuses an existing
  Talon window instead of launching a second instance. Talon manages its own
  sign-in; this plugin does not share its cookie with Talon.
- Once the ship is signed in, the plugin also adds **Tlon Web (~ship)** to
  Omarchy's app launcher (Super+Space). It invokes the same authenticated
  browser handoff, never a plain URL that might open signed out. The plugin
  updates the entry on account change and removes its own entry on sign-out;
  it will not overwrite a launcher someone else created. Omarchy does not
  run plugin uninstall hooks, so if you remove the plugin without signing out,
  remove `~/.local/share/applications/urbit-dms-tlon-web.desktop` manually.
  There is no separate global keybinding; assign one in Hyprland if desired.
- **Esc** closes the quick DM, inbox, or bar popup.
- Sign in with your ship's URL and the output of `+code`. Enter a local ship as
  `http://localhost:8080`. HTTPS is recommended for remote ships; when you
  explicitly enter `http://` for a LAN ship the login code crosses the network
  unencrypted.
- Search by nickname or `~ship`. Quick DM shows fuzzy matches from contacts
  and conversations; it also offers an explicitly labeled exact `~ship` choice
  for a complete ID that isn't already a contact.
  Select a result explicitly before sending, so ambiguous nicknames cannot
  silently pick the wrong recipient.
- Archived conversations and invitations appear in the inbox; invitations must
  be accepted before replying. The inbox includes every DM returned by the ship,
  without a search-result cap. Threads load their latest page first and load
  older pages automatically when you scroll to the top.
- Invitation-acceptance notices appear as centered conversation events, not
  as messages you sent. Other `%chat` notices are likewise kept out of the
  outgoing-message preview styling.
- Your `+code` is never saved. An origin-bound Eyre session cookie is saved in
  Secret Service under application `omarchy-urbit-dms`; nonsecret account URL
  and `~ship` are stored in `$XDG_STATE_HOME/omarchy-urbit-dms/account.json`.
  Restarting the shell restores the session automatically; **Sign out** clears
  its keyring entry. An expired/revoked cookie requires signing in again.
  The theme plugin uses the same Secret Service, but its credential is separate:
  signing out of either plugin does not clear the other's keyring entry.

The plugin subscribes to `%chat /v4` for messages and `%activity /v5` (falling
back to `/v4`) for notifications, and observes `%contacts /v1/news` for names.
It only fetches a small inbox summary at sign-in; a thread's first 20 messages
load when opened, with older messages on demand. The full contacts directory
loads only when searching. Desktop notifications honor `%activity`'s
`notified` flag and do not include message content in the popup.

## Install

```sh
omarchy plugin add https://github.com/thelifeandtimes/omarchy-urbit-dms.git --enable
```

The bar widget can be placed during installation. To use the quick-DM and inbox
keyboard shortcuts described above, add bindings for the two `omarchy-shell
shell summon` commands below; the plugin does not install global keybindings.

## Development install

From this checkout, make a symlink under `~/.config/omarchy/plugins/urbit.dms`
pointing at the repo, then run:

```sh
omarchy-shell shell rescanPlugins
omarchy plugin enable urbit.dms
```

When developing through a symlink, restart the shell after changing the
plugin's source (`omarchy restart shell`); the plugin file watcher does not
follow the symlink into this checkout.

The keybindings use `omarchy-shell shell summon urbit.dms '{"mode":"quick"}'`
  and `omarchy-shell shell summon urbit.dms '{"mode":"inbox"}'`. They are
included in the local development setup; if installing elsewhere, add them
to `~/.config/hypr/bindings.lua` yourself.

## Checks

```sh
python3 -m unittest -v test_bridge.py test_session_store.py test_external_apps.py test_launcher_entry.py
node test_search.js
node test_time.js
qmllint Dms.qml BarWidget.qml RecentPanel.qml Identity.qml NameModeSwitch.qml ChatMark.qml State.qml
omarchy plugin validate .
```

Text-only sending is intentional; media, group DMs, rich-content editing and
push notifications while Omarchy shell is not running are not implemented.
