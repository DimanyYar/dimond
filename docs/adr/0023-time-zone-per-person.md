# 0023. Each person's own time zone, from the tzdata package

Status: Accepted, 2026-10-26; amended 2026-10-27

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

## Amendment, 2026-10-27

People who signed up before time zones existed never saw the Welcome screen, and the Account
page only suggested the browser's zone without saving it, so their pages kept showing UTC.
Until a person saves a zone, pages now show the browser's: `static/js/browser-zone.js` puts
its name in the `browser_time_zone` cookie on every page, and the middleware uses it when it
is one from the list (anything else in the cookie is ignored). A saved zone always wins; the
team still sees UTC. The very first page after signing in on a new browser may still show
UTC, before the cookie exists.
