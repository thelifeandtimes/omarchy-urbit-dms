# Urbit DMs 0.2.0

Adds optional Omabit application connections while retaining standalone URL/+code
sign-in. Each approved plugin receives its own Eyre session and communicates
directly with the ship; Omabit is not an application-data proxy.

The menubar and sign-in UI now share native header, typography, spacing, action
and color conventions with the other reference plugin. Connect with Omabit is
the primary sign-in action and direct credentials remain available. Revocation
returns the interface to sign-in; reconnect and interrupted cleanup retain their
durable journals. Private development profiles isolate credentials, state and IPC.

Existing uncertain outbound actions are preserved and are not silently retried.
Normal plugin updates retain preferences and credentials, including interrupted
outbound journals and direct sign-in state. This release does not claim desk-scoped Eyre authorization, hostile
plugin isolation, or completion of the planned security/privacy review.

Validation includes Python transport/session/installation fixtures, real Qt/native
control fixtures with inert I/O, and paired light/dark/narrow/long-identity popup
and sign-in renders. Live DEV authentication/revocation and private fake-ship
transport were qualified with the accompanying Omabit0.3.0 work.
