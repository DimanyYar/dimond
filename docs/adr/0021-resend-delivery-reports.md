# 0021. Resend's delivery reports through a signed webhook

Status: Accepted, 2026-10-22

## Context

The email journal (ADR 0009) knows only whether Resend took an email. The PM also needs to
know what happened next (PRD: email notifications). A mailbox that does not exist should show
in Logs · Emails. An email marked as spam must stop that person's emails: more spam marks
would hurt the sending domain for every client.

Resend reports this by calling a webhook. The calls are signed through Svix: a secret shared
with Resend signs every body, together with a timestamp. Without checking the signature,
anyone who finds the address could switch any client's emails to Nothing with a forged "spam"
report.

## Decision

- Receive the reports with django-anymail's own Resend webhook view at
  `POST /resend/webhook/`. anymail checks the signature with the `svix` package, installed as
  the `django-anymail[resend]` extra, and answers 400 to a wrong one.
- The secret comes from `RESEND_WEBHOOK_SECRET` in `.env`. Empty means the address answers
  "not found", so a laptop without Resend has no open webhook.
- Three reports are kept: delivered, bounced and marked as spam (complained). Sent, delayed,
  opened and clicked are dropped: opens and clicks would mean tracking pixels and rewritten
  links in a client's email.
- A report changes the email's own row in the journal, found by the id Resend gave it. There
  are no extra rows per report. The status only moves forward: Sent, then Delivered, then
  Bounced, then Marked spam. A late "delivered" never hides a spam mark, and a spam mark after
  a bounce still stops that person's emails. A report for an id not in the journal, such as
  another copy of dimond on the same Resend account, is ignored.
- A bounce is only logged, with the reason in plain words and Resend's own words on hover.
  dimond keeps emailing that address: Resend itself stops after repeated bounces, and then
  says so ("suppressed").
- A spam mark switches that person's emails to Nothing and stores when it happened. Their
  account page and the PM's person page say so until someone picks an option other than
  Nothing. Sign-in links still go out.

## Consequences

+ The PM sees "Delivered" or why an email did not arrive without opening Resend.
+ A forged report needs the signing secret; rotating it is a change in Resend and in `.env`.
− One more package: `svix` (MIT, maintained by Svix, the company that sends Resend's webhooks).
  If it is abandoned, the check is about 20 lines of HMAC-SHA256 from Svix's published spec;
  anymail would then accept a basic-auth secret in the address instead.
− Reports only arrive once dimond has a public address (#21). Before that they are tried with
  a body signed in the tests.
− A spam mark from a client's filter, not from the person, still switches them to Nothing.
  They see why on their account page and can switch back.
