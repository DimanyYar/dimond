# 0003. Passwordless sign-in by email link

Status: Accepted, 2026-10-15

## Context

Shared secret links per company were rejected: they get forwarded, stay in chat history,
do not show who wrote a comment and can only be revoked for the whole company. Passwords mean
reset flows and reused weak passwords.

## Decision

Everyone signs in with a one-time link sent to their work email: valid for 15 minutes
(invite links: 7 days), stored only as a hash, session lasts 30 days. Access is per person and
revocable per person. The sign-in page never reveals whether an address has an account.
Two-factor (TOTP via django-otp) for the PM and the Django admin is added before the second
client company is onboarded; the sign-in pipeline leaves a step for it from day one.

## Consequences

+ No passwords to leak, reset or reuse; access to email is the recovery.
− Email deliverability becomes critical: a domain with SPF, DKIM and DMARC is required before
the first deploy. Until 2FA lands, a compromised PM mailbox exposes the pilot client's data.
