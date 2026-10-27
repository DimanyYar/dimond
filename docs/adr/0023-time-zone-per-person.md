# 0023. Each person's own time zone, from the tzdata package

Status: Accepted, 2026-10-26

## Context

Times are stored in UTC (CLAUDE.md) and, until now, were also shown in UTC. Clients and the PM
live hours apart (US Pacific and Moscow), so "Oct 26, 22:30" meant a different moment to each
of them. Python's `zoneinfo` knows time zones only when the system has a time zone database or
the `tzdata` package is installed; the `python:3.13-slim` image is not guaranteed to have the
first, and `tzdata` arrived only by chance, through django-anymail[resend].

## Decision

- `User.time_zone` holds an IANA name such as `Europe/Moscow`; empty means UTC.
- Clients and the PM pick it in Account; a newcomer picks it on the Welcome screen. Both
  lists start at the browser's own zone, so most people only confirm it. The team has no
  account page and sees UTC.
- A middleware turns the signed-in person's zone on for each page, so every `|date` in the
  templates converts on its own. Nothing else changes: storage, emails, the worker and Jira
  stay in UTC.
- **tzdata** becomes a direct dependency, so the zones are in the image whatever else is
  installed.

## Consequences

+ Everyone reads times as their own clock shows them; "today" on Requests is their today.
+ Adding the zone to a new page costs nothing: templates convert by themselves.
− One more dependency, maintained by the Python core team and updated with each zone change.
  If it ever goes, installing the system `tzdata` in the Dockerfile does the same job.
− People who signed in before this see UTC until they save a zone in Account; the page tells
  them so.
