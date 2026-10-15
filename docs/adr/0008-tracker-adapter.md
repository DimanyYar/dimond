# 0008. Tracker adapter with Jira and a mock

Status: Accepted, 2026-10-15

## Context

Jira Server 8.13 is the first tracker. Tests and the public demo must run without it, and
other trackers (YouTrack, Linear) are plausible later.

## Decision

All tracker calls go through one adapter interface with two real implementations from the
start: `jira` (REST v2, Basic Auth with a service account) and `mock` (in-memory, used by tests
and the demo). The rest of the app never imports a Jira client directly.

## Consequences

+ Tests and demo need no network; a new tracker is a new adapter, not a rewrite.
− One extra layer of indirection around tracker calls.
