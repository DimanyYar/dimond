# 0002. Django monolith with HTMX

Status: Accepted, 2026-10-15

## Context

One PM and a handful of client companies. The app needs sign-in, sessions, forms, an ORM,
migrations and an internal admin. FastAPI was considered: strong for APIs, but all of the above
would have to be assembled by hand.

## Decision

One Django 5.2 LTS project, server-rendered templates, HTMX for partial updates and a few
small JS modules (voice and screen recording, the media player, paste from clipboard).
PostgreSQL as the database. No separate SPA.

## Consequences

+ One codebase, one deploy, built-in auth, admin and migrations; any backend developer can
read it.
− Rich client-side interactions cost more than in a SPA. A JSON API for a mobile app or widget
will be added on top when needed.
