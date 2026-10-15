# 0010. What dimond reads from and writes to Jira

Status: Accepted, 2026-10-15

## Context

Jira comments are internal; leaking one to a client is the most likely privacy incident.
The PM rewrites the task description into a full spec after creation.

## Decision

dimond writes to Jira only (1) a new task created by the PM and (2) a client's answer to a
team question, as a comment quoting the question and mentioning its author. It reads statuses,
fix versions and comments that mention the dimond service user. Descriptions are never synced
in either direction. Polling every 15 minutes is the source of truth; a Jira webhook only
triggers an early fetch and its payload is not trusted.

## Consequences

+ Internal discussion never reaches clients by accident; the PM edits Jira freely.
− Team questions must use the @dimond mention, which needs a short team agreement.
