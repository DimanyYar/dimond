# 0025. Jira formatting is read by dimond, never taken as HTML

Status: Accepted, 2026-10-27

## Context

The team writes questions for the client (ADR 0010) in Jira's wiki markup: `*bold*`,
`||tables||`, `{code}`, `[words|address]`. dimond turned them into plain text, so tables and
stars reached the client raw. The client should see the question formatted as in Jira.

Jira can return a comment as its own HTML (`renderedBody`). That HTML holds Jira's internal
addresses, emoticon pictures from the Jira server and whatever its macros produce, and
showing it safely would need an HTML sanitiser (`nh3`, a new dependency). Converting the
markup with `jira2markdown` would add that package, a Markdown renderer and the sanitiser.

## Decision

- **dimond reads the markup itself** (`apps/tracker/jira_markup.py`), no new dependency.
  It writes HTML only from its own short list of tags; every word of the comment is escaped,
  so `<script>` typed in Jira is shown as text. No inline styles, so a Content Security
  Policy (#22) needs no exceptions.
- **The comment is kept as written** (`Message.team_markup`, tag removed) and formatted when
  the thread is shown, never stored as HTML: a fix in the reader reaches every old question,
  and there is no stored HTML to tamper with. The same reader gives the plain text for
  previews and for the quote in the client's answer.
- **Only `http`, `https` and `mailto` addresses become links**, opened in a new tab with
  `rel="noopener noreferrer nofollow"`. Links to the Jira server itself stay words: the
  client cannot open them.
- **What is not formatted is shown by its words**: colour and panels by their contents,
  mentions as `@login`, unknown macros as typed. Pictures and file links stay files beside
  the text, as before.
- Questions copied before this change get their markup from the next sync quietly: it is not
  an edit, the status does not change and nobody is told.
- Done in two steps (#28): styles in a line first, then lists, tables, code blocks, quotes
  and headings.

## Consequences

+ No dependency to keep up; every tag the client can receive is in one file.
+ Old questions improve with the reader, without a migration of their text.
− Jira's markup has corners the reader does not know; they show as typed, not broken.
− Questions on tasks the sync no longer follows keep their plain text.
