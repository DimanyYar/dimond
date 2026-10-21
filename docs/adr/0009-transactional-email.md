# 0009. Transactional email via django-anymail and Resend

Status: Accepted, 2026-10-15; amended 2026-10-21

## Context

Sign-in links and notifications must reach inboxes. SendGrid no longer has a free plan.
Resend's free tier (3,000 emails a month, 100 a day) covers the expected volume.

Running our own mail server was considered and rejected on Oct 21, 2026: many hosts block
outgoing port 25 on new servers, a fresh server address has no reputation, so mail lands in
spam, and the delivery reports the PM needs ("bounced", "marked as spam") would mean
receiving and parsing mail too. The server load was never the problem; deliverability was.

## Decision

Send through django-anymail with Resend as the provider, switched on by `RESEND_API_KEY`.
Without the key, mail goes over plain SMTP: to Mailpit locally. The provider account is
registered on a work email. Emails are no-reply; people answer inside dimond.

Every attempt to send is written in an `Email` journal (added Oct 21, 2026): who, which kind,
the subject, sent or not and why, and the id Resend gave it. Never the body, so no sign-in
link or message ends up there.

## Consequences

+ Switching to Brevo, Postmark or Amazon SES is a settings change, not a code change.
+ Delivery reports from Resend can later be matched to journal rows by their id.
− One more dependency (django-anymail), and with it `requests`, a second HTTP library next to
  httpx; a verified sending domain is required.
− If django-anymail is abandoned, Django's own SMTP backend can send through Resend's SMTP
  relay; only the provider id in the journal and the delivery-report webhook would need
  replacing.
+ Tries of one email share its row: "Not sent · 7 tries" until it goes out (amended Oct 21,
  2026). After a minute of nothing but "server unreachable" or "daily limit" failures,
  emails pause: one email is tried every 5 minutes, Logs shows a banner with Try now, and
  the first email that goes out ends the pause. Never a full stop: sign-in links are the
  only way in, the PM's included.
− While paused, waiting emails go out up to 5 minutes after the server is back, unless
  the PM presses Try now.
