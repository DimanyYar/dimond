# 0005. Business logic in services, reads in selectors

Status: Accepted, 2026-10-15

## Context

The same action is triggered from several places: creating a Jira task runs from the PM's
button, from a retry after a Jira outage and from the admin. Clients must never see another
company's data, and a single forgotten filter is enough to leak it (IDOR).

## Decision

Inside each app: `models.py` holds data, `services.py` holds every action with its
transaction, `selectors.py` holds every read and scopes it to the acting user, `views.py` only
parses input, calls one service or selector and renders. Views never query models directly.

## Consequences

+ One recipe per action, testable without a browser; tenant isolation lives in one place.
− More files than the classic fat-model or fat-view layout.
