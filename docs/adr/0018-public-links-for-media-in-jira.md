# 0018. Public links for video and voice in Jira tasks

Status: Accepted, 2026-10-19

## Context

Files open only after an access check (ADR 0013), and the team was to sign in with the Team
role to watch a client's video from a Jira task. Dmitry decided on Oct 19 that anyone who
opens the task (a developer, QA, a client with access to our Jira) should play the video or
voice from the task's link without signing in. Pictures and PDFs go into Jira as files, so
they need no link.

## Decision

- A video or voice sent to a Jira task gets a public link `/p/<secret>/`: 32 random bytes,
  which cannot be guessed, stored on the attachment and made once, so links already in Jira
  keep working.
- The link works while the request is open, so while its task is. When the request closes,
  the link closes too. The PM can open it again from the card for 7 days at a time.
- Only files the PM chose to share have a link; every other file still needs the access check.
  The answer carries `Referrer-Policy: no-referrer` and `X-Robots-Tag: noindex`, and no cache
  keeps it.

## Consequences

+ The team watches a client's recording from the task in one click, without a dimond account.
− Whoever gets the link while it works (a forwarded Jira email, a screenshot) can play the
  file. Recordings of the CRM may show the client's customers, so the link closes with the task
  and reopens only on purpose, for a week.
− A shared link cannot be taken back while the request is open; a "reset link" button is a
  later step if it is ever needed.
