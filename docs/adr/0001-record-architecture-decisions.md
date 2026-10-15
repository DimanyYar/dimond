# 0001. Record architecture decisions

Status: Accepted, 2026-10-15

## Context

Decisions that are expensive to reverse (framework, database, how people sign in) get lost in
chats and memory. Six months later nobody remembers why there is no Celery.

## Decision

Keep a short record per decision in `docs/adr/`, numbered, in the format of this file:
context, decision, consequences. Records are never rewritten: a changed mind is a new record
that supersedes the old one.

## Consequences

+ New contributors and reviewers can see why, not only what.
− Writing a record takes ten minutes. Only decisions that are costly to undo get one.
