# 0016. Signed-in devices are recorded at sign-in

Status: Accepted, 2026-10-18

## Context

The PM's person page shows where someone is signed in and offers "Sign out all devices"
(PRD: person page). Django's session table does not say whose session is whose: the user id
sits inside the encoded session data. ADR 0015 already ends every session by changing the
password fingerprint, but that cannot list devices.

## Decision

A `Device` row is saved each time someone signs in with a link or an invite: the session key,
the browser's User-Agent (cut to 300 characters) and the time. The page shows it as
"Chrome on macOS", read from the User-Agent by a short table of known browsers and systems,
not a parsing library. Only rows whose session still exists and has not expired are listed.
Signing out on a device removes its row; a new sign-in removes the person's rows whose
sessions are gone.

"Sign out all devices" uses the same means as revoking (ADR 0015): a new unusable password,
so every session ends, including ones from before this table existed. It also deletes the
person's known session rows and devices so the list is empty at once. Access stays: the
person signs in again with an email link. Revoking does the same, plus turning access off.

## Consequences

+ No new dependency; the list is honest because it is checked against live sessions.
+ One way of ending sessions for revoke, the admin action and "Sign out all devices".
− Names are rough: an unknown browser shows as "Browser", a rare one may be misnamed. Good
enough to tell a laptop from a phone, which is all the PM needs.
− Sessions signed in before this change are not listed, though "Sign out all devices" still
ends them.
− The User-Agent is personal data in a mild form. It is shown only to the client's own PM and
removed when the person signs out there, signs in again after the session ended, or is
signed out by the PM.
