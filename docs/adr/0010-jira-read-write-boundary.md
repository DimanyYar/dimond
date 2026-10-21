# 0010. What dimond reads from and writes to Jira

Status: Accepted, 2026-10-15

## Context

Jira comments are internal; leaking one to a client is the most likely privacy incident.
The PM rewrites the task description into a full spec after creation.

## Decision

dimond writes to Jira only (1) a new task created by the PM, (2) a client's answer to a
team question, as a comment quoting the question and mentioning its author, and (3) when the
PM links an existing task and leaves "Add a comment" ticked, one comment pointing back to the
request with its files (added Oct 19, 2026). It reads statuses, fix versions and the team's
questions for the client: comments tagged `#client` outside a quote, with the files they show
(changed Oct 20, 2026, from comments mentioning a dimond service user). A plain-text tag needs
no service account; a mention would have pointed at Dmitry while the sync signs in as him, and
every mention of him would have reached the client. dimond's own answers quote the question,
so the tag inside a quote never counts. The answer goes as soon as the client sends it, without the
PM (decided Oct 20, 2026); the client's words are escaped so they never mention anyone or
open a new question. If Jira does not take it, nothing retries by itself: the PM sees why and
sends it again. Descriptions are never synced
in either direction. Polling every 15 minutes is the source of truth; a Jira webhook only
triggers an early fetch and its payload is not trusted.

## Consequences

+ Internal discussion never reaches clients by accident; the PM edits Jira freely.
+ Linking adds a comment rather than editing the description: Jira 8.13 can only replace a
description whole, so appending would risk erasing an edit made at the same moment.
− Team questions must carry the `#client` tag, which needs a short team agreement; a comment
written without it stays internal, which is the safe way to get it wrong.
