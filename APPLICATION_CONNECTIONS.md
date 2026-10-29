# Experimental application connections

This development branch adds a private-stdin receiver for an independently
issued Eyre session. The unreleased Omabit approval flow has passed isolated
CLI/panel/receiver tests; it is not installed in the active desktop. Normal
URL + `+code` login remains standalone and requires no Omabit binaries.

The receiver validates plugin/profile/connection/request binding, an origin-bound
session and expiry, checks both public and authenticated ship identity, verifies
Secret Service storage, and commits private account metadata before returning a
nonsecret acknowledgement. It refuses to overwrite an existing account. The
issuer must retain cleanup ownership until it durably records acceptance, and
revoke its newly issued session on an uncertain or failed handoff.

The session is an ordinary owner Eyre session, not a desk capability. The plugin
connects directly to its ship after handoff. No application traffic passes through
Omabit. Backend installation and desktop/theme changes are not part of accepting
a credential.

Optional experimental instance metadata lives in `application-profile.json`:

```json
{"version":1,"profile":"auth-lab"}
```

It requires the canonical manifest ID with `.auth-lab` appended, and separates
state and keyring application names. A mismatched manifest is refused. The
Omabit test harness stages this metadata in copied artifacts; do not change the
current installed plugin's identity or select its production credentials.

This namespace support alone does not isolate all desktop effects, IPC, hooks or
launchers. Initial experiments run headlessly with private HOME/XDG roots and
a private Secret Service in a network/process/filesystem namespace. Enabling an
experimental UI beside a normal installation remains a separate checkpoint.

Source protocol: Omabit `docs/application-handoff-v1.md`. No shared client package
has been extracted yet. The receiver intentionally has no runtime dependency on
Omabit; it continues using this plugin's own storage and direct transport.


## Interrupted handoff recovery

The receiver writes nonsecret intent in `application-attempts.json` before the
first possible keyring write. After confirming remote cleanup, the issuer may
invoke `application_connection.py --reconcile` with the exact attempt binding,
origin and ship on stdin. This removes only that attempt's session/account; a
newer standalone login survives. A repeated completed recovery returns its saved
receipt without touching a later account. No cookie is passed to reconciliation.

Recovery additionally requires `/usr/bin/busctl` for Secret Service `SearchItems`
metadata. A missing item, locked item and unavailable service are distinct; locked
or ambiguous results remain pending. `secret-tool` still handles the secret.
There is no plaintext fallback. Active journals retain up to 64 attempts.
Reconciled attempts are now compacted into private `application-history` receipts
before removal from the active list. Receipts retain the binding/fingerprint and
outcome, never the session cookie, and prevent new helpers accepting an old offer.
They are permanent replay evidence; total history grows with completed requests.

Pending and accepted records stay active. A new helper also compacts a full legacy
completed journal before accepting a new request. The active shape stays version 1
so an older retained helper can finish cleanup of its remaining account. Older
helpers do not understand archived receipts; they rely on Omabit's version-2
issuer journal refusing replay. Do not manually redeliver old offers. Omabit only
offers archival once remote cleanup and receiver reconciliation are complete.

Omabit's verified helper launcher retains an approved generation before issuance
and launches a private copy with bounded stdin/stdout. This is artifact pinning,
not isolation from malicious plugins running as the same Unix account. The real
private-lab gate now covers lost acknowledgements, remote revocation, account
reconciliation and a subsequent fresh approval for both Theme and DMs. The unreleased Omabit approval panel now has isolated fixture coverage; the
complete declared-helper/UI runtime path and full profile switching remain
separate qualification gates.


## Installed declaration

`application-connection.json` advertises the Python receiver, ordinary owner-session
authority and its complete private helper inventory. Omabit reads it only from a
locally installed plugin directory matching the manifest ID; it hashes the
declaration, manifest, dependencies and optional profile metadata before approval.
This adds no catalog, backend installer or Omabit runtime dependency to the plugin.
Source copies with separate `.auth-lab` IDs have passed inventory/import checks.
The current installed plugin has not been modified or enabled for this experiment.


## Public-flow qualification

Omabit's fresh-fake run `20261021-08` passes the actual approval panel and compiled
collector with both real declared receivers, private Secret Service and separate
`cli-lab`/`ui-lab` copies. Direct plugin reads/SSE continue after the panel exits;
disconnect/reconciliation is independent, including after parent logout/removal.
An already running Theme service (desktop adapter disabled) and DMs bridge (native
launcher adapter disabled) adopt the newly approved accounts. No installed plugin,
production ship, desktop or keyring was used. Full native overlay/hooks/profile
integration and data writes/reconnect still need
their gates. See Omabit `docs/diagnostics/2026-10-21-public-auth-flow.md`.

Opening the inbox or refreshing restores a saved account if the running bridge
has no active session. Quick-open requests the same restore explicitly. This
handles approvals received after startup without another +code. The auth lock
serializes restoration; an active client is never silently replaced, and a locked
keyring produces an error rather than a plaintext/login fallback.


Stream actions queued before a selection change are now bound to their original
account generation and channel. Reconnect cleanup cannot delete a replacement
channel, and late contacts reads cannot overwrite a new account's book. The
`test_stream_generation` and `test_bridge` suites cover these regressions offline;
this does not establish complete real-server or collection-switch qualification.

The native transport control01 now passes real DMs in both directions across a
fresh fake receiver restart, with original sessions, new subscriptions and retained
history. Its frozen bridge includes the stale-stream fixes. A later offline fix
now requires exact positive Gall receipts, integer request IDs and first-result
preservation; all 28 affected tests pass. The later source needs a fresh runtime
control. See Omabit diagnostics/2026-10-22-plugin-transport-native.md for limits.

Fresh native control02 also passes with that stricter receipt source. Independent
checks bind the four DM writes to exact wire poke IDs/channels, positive receipts,
receiver events and both histories. This supersedes the pending newer-source
runtime statement above; full desktop/profile activation remains separate. See
Omabit diagnostics/2026-10-22-plugin-receipts-native.md.

## Qualification status (2026-10-24)

The later native control02 passes direct writes and reconnect with strict positive
receipts; this supersedes earlier pending transport statements above. The current
installed plugins remain unchanged. Full desktop/profile activation and hosted
provider qualification remain open. See Omabit
`docs/diagnostics/2026-10-22-plugin-receipts-native.md`.

Hosted-style standalone login now accepts an explicit same-host cookie Domain,
which Python represents with a leading dot. It requires exactly one root-scoped
Eyre cookie and rejects parent domains, expired cookies and ambiguous sessions
before saving an account. The regression uses the real standard-library cookie
parser with inert responses; 33 related tests pass. This is source evidence, not
a claim that a hosting provider or its TLS/gateway behavior has been tested.

Experimental profile launchers now use separate filenames, visible labels and
instance ownership markers. Temporary-directory coexistence/removal tests pass;
QML/IPC and complete collection switching remain separate gates. This supersedes
the launcher-filename collision only, not the broader desktop isolation warning.


The QML overlay and bar popup now follow the host-injected instance ID, including
late injection. Stable/dev/edge copies have distinct labels, layer namespaces,
open/close targets and directory-local singleton state. Networkless QML tests with
inert process/file/IPC adapters pass; existing contact-name rendering is preserved.
This supersedes the pending QML routing portion above, not native compositor,
real IPC, hooks or complete collection switching. The installed plugin is unchanged.
