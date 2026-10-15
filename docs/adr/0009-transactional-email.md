# 0009. Transactional email via django-anymail and Resend

Status: Accepted, 2026-10-15

## Context

Sign-in links and notifications must reach inboxes. SendGrid no longer has a free plan.
Resend's free tier (3,000 emails a month, 100 a day) covers the expected volume.

## Decision

Send through django-anymail with Resend as the provider. Locally, mail goes to Mailpit.
The provider account is registered on a work email.

## Consequences

+ Switching to Brevo, Postmark or Amazon SES is a settings change, not a code change.
− One more dependency (django-anymail); a verified sending domain is required.
