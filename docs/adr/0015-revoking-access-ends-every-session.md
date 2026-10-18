# 0015. Revoking access ends every session for good

Status: Accepted, 2026-10-18

## Context

Revoking access turned `is_active` off, which signs a person out on their next page (ADR
0011). Their sessions stayed valid in the database, so giving access back (Re-invite on the
Clients page) brought every old device back in: a laptop left with a former colleague, a
shared computer. Deleting sessions needs to know whose session is whose, which Django's
session table does not store.

## Decision

Revoking also gives the person a new unusable password. Django stores a fingerprint of the
password in every session and checks it on each request, so all sessions signed in so far
stop working at once and never come back, including ones created before this change. Unused
sign-in and invite links are deleted, so an old link cannot open the door after access
returns. Re-invite turns access on and sends a fresh 7-day invite; the person signs in anew on
each device.

## Consequences

+ No new table: one field change covers every device, old sessions included.
+ "Revoke" means the same in the Clients page and the Django admin action.
− The session rows stay until they expire; they are dead, just not removed. Since ADR 0016
the rows of recorded devices are removed too.
− Revoking a superuser also removes their admin password; they need `changepassword` to use
the admin again. PMs who own clients cannot be revoked at all (ADR 0014).
