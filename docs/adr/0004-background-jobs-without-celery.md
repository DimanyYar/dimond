# 0004. Background jobs without Celery

Status: Accepted, 2026-10-15

## Context

Background work is a Jira sync every 15 minutes, webhook follow-ups and emails: tens of jobs
an hour, not thousands a minute.

## Decision

A separate `worker` container runs Django management commands on a schedule. No Celery, no
Redis.

## Consequences

+ Two fewer services to run, monitor and secure; jobs are plain functions that are easy to
test.
− No retries with backoff or fan-out out of the box. Revisit when the email queue starts to lag
or jobs need parallelism.
