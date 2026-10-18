# 0014. Every PM has their own clients

Status: Accepted, 2026-10-18

## Context

dimond started with one PM who saw every client. Other PMs at Shakuro may try it, each with
their own projects and clients; they never work with each other's clients. Every PM-facing
read so far meant "all requests", and each new PM feature (Jira tasks, sync logs, emails,
reports) would add more such places.

## Decision

A company belongs to one PM: `Company.owner`, required, limited to people with the PM role.
`requests_for` gives a PM only the requests of the companies they own; files, the requests
page, chips, search and the card all read through it, and every PM action also checks the
owner. The migration gives existing companies to the first PM (the superuser first) and stops
with a hint if companies exist but no PM does. The Django admin still shows everything: it is
the superuser's tool.

## Consequences

+ A second PM signs in to an empty portal and sees only the clients they add; a guessed
request id answers 404, as for clients of another company.
+ New PM features scope by owner from the start instead of being fixed later.
− Handing clients to a colleague for a holiday means changing the owner in the admin for now;
shared or temporary access is a later decision.
