# 0011. People, roles and companies in one user model

Status: Accepted, 2026-10-15

## Context

Three kinds of people sign in: client staff, the Shakuro team (developers and QA who open
attachment links) and the PM. Sign-in is by email, never by username or password (ADR 0003).
Django's default user model has a username and assumes passwords, and replacing the user model
after the first migration is painful.

## Decision

A custom `User` from the first migration: email is the login (stored lower-case, unique), a
`role` field (client, team, PM) and a `company` link. A database constraint enforces that
clients belong to exactly one company and team members and the PM to none. People get no
usable password; only the superuser created with `createsuperuser` has one, for the Django
admin. Revoking access sets `is_active` off; companies with people cannot be deleted, so
requests and replies are never orphaned.

## Consequences

+ The rules "a client always has a company" and "one email, one person" hold even if a future
bug skips validation.
− A person who works for two client companies needs two emails. Revisit with a membership table
if that ever becomes real.
