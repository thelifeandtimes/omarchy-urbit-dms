# Urbit DMs for Omarchy

Development note: [independent application sessions](APPLICATION_CONNECTIONS.md) are being qualified in isolated plugin instances; the normal standalone login remains available.

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

Experimental application-connection recovery also uses `busctl` to distinguish
missing keyring items from locked or unavailable ones. Standalone login remains
independent of Omabit. See [the connection contract](APPLICATION_CONNECTIONS.md).

### Isolated profile UI check

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/qml/run_profiles.py \
  --output /tmp/omabit-dms-profile-qml-check01
```

Use a fresh output directory on each run. This requires Bubblewrap, Qt's
`qmltestrunner` and Omarchy shell source at `/usr/share/omarchy/shell`. It copies
QML into stable/dev/edge roots and uses real native controls with inert file,
process and IPC adapters. The namespace has no network, host home, desktop bus or
compositor sockets. No Python plugin helper is copied. Any QML warning fails the
check; source and fixture hashes accompany its results. There is no host fallback.

This checks profile labels, routes, late host injection and separate QML singleton
state. It does not qualify native Wayland placement, real IPC or profile activation.

### Uncertain sends and profile changes

Before a DM send, the bridge saves the origin, author, recipient, exact message ID
and private draft in `outbound-messages.json` under this instance's XDG state
folder (directory0700/file0600). No +code, session cookie or keyring value is stored
there. A missing HTTP/Gall reply stays uncertain; helper restart and profile
switching do not resend it. The UI keeps a separate unresolved-send indicator
when its ordinary busy flag clears. Opening the conversation can resolve an exact
ID/author/time/text match from that same ship and origin. Absence from a page does
not prove rejection. Positive acknowledgements and explicit rejections resolve
records; unresolved entries are never discarded to make space.

This journal covers DM sends. Invitation responses, read markers, graceful
subscription cleanup and a full collection-switch coordinator are separate work;
an empty send journal alone is not permission to unload a plugin.

The bridge has an internal `shutdown-transport` command for a future collection
coordinator. It pauses new commands, drains existing workers, deletes its exact
channel, waits for listeners to exit and returns unresolved-send references without
removing the session. Failed deletion retains the exact channel for retry. Its
receipt explicitly says `collectionReady: false`: it covers transport closure,
not all app effects or general profile activation. The QML `stopTransport()` method
exposes this opt-in operation; ordinary close/hide behavior is unchanged.

Invitation acceptance and read markers also keep private, versioned intent in
`conversation-effects.json` before dispatch. Only an exact positive Gall receipt
or explicit rejection resolves an entry. Unknown actions survive helper/profile
replacement and are reported in the transport receipt without automatic replay.
Read markers no longer clear local unread counts on HTTP success alone; a late
receipt from an earlier connection cannot modify the current ship's display.
Unknown formats and full unresolved history refuse new actions without discarding
records. Authenticated refresh can confirm the desired state: an active DM confirms
local invitation acceptance, and an explicit `unread: null` activity summary confirms
the main stream is read. This records `state-confirmed`, not a recovered operation
acknowledgment. Missing/malformed data, another ship/origin, or a read started before
the intent cannot resolve it. Version1 journals are read unchanged and migrate to
version2 on the next write; unknown future versions are preserved and refused.


When the shell closes the bridge's input pipe, the bridge now runs the same
transport drain used by explicit shutdown, including on a broken output pipe.
Unconfirmed final cleanup exits nonzero; it does not silently report success.
Private tests cover EOF and shell loss with channel deletion. The development
bridge also keeps version1 channel ownership in `transport/channels.json`, with
no credentials or message bodies. After verified helper death, it deletes the
exact saved channel using its existing session before opening another. A lost
delete reply keeps the original cleanup request and pauses new application writes;
uncertain messages and conversation effects are never replayed. Missing/unknown
journal data and unknown process ownership refuse intact. A competing helper
cannot acquire the lifetime transport lock.

Actual helper kills, lost deletion replies and restored subscriptions pass against
a private synthetic HTTP peer/keyring, alongside147 unit tests. This is not yet
actual-Urbit or installed-desktop qualification. Old helpers without this journal
must drain before adoption; rollback to a reader that ignores it is unsupported.
Missing/changed/expired credentials still require a recovery contract. The journal
preserves up to1024 channel receipts and refuses new channels when full; supported
archival is pending. Collection uncertainty remains until independently verified
recovery; transport closure alone never sets `collectionReady`.


Fresh actual-Urbit qualification now also passes helper kills, a lost successful
delete reply and subscription recovery with the same saved session. It exposed
that remote channel deletion need not wake the client's blocked event-stream read.
The bridge therefore shuts down an owned duplicate of the exact stream socket
before joining; late or stale readers cannot touch a replacement. All150 private
tests and the real-Urbit final drain pass, including404 checks for both deleted
channels. Hosted TLS/gateway behavior and independent collection-recovery approval
remain unqualified. The preceding synthetic-peer-only limit is superseded for this
fresh-fake transport path, not for every application mutation or production setup.
