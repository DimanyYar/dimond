# 0017. httpx for tracker calls; the tracker is switched in the admin

Status: Accepted, 2026-10-19

## Context

The Jira adapter (ADR 0008) is dimond's first outgoing HTTP client. It sends JSON, will upload
files (#15), and its tests must never reach the network: they replay sample Jira responses.
Shakuro may move from Jira to another tracker; Dmitry wants to switch trackers without a
deploy, while keeping the address and password out of the database.

## Decision

- **httpx** makes the calls. It gives timeouts, Basic Auth, multipart uploads and, most
  usefully, `MockTransport`: tests hand the real `JiraTracker` a fake network that answers with
  saved JSON, so the adapter's own code runs in tests, not a stand-in. Python's `urllib` has
  none of that without hand-written plumbing, and `requests` needs a second library to fake
  the network.
- Redirects are never followed and the address must be https: Basic Auth sends the password
  with every call (Jira 8.13 has no tokens), and Jira reports its own address as http.
- `TrackerSettings`, one row in the admin, chooses the adapter: Jira or the built-in demo.
  The address and account come only from `.env` (`TRACKER_JIRA_*`). A new tracker means a new
  adapter and a new choice in that list.

## Consequences

+ The adapter is tested end to end without Jira; switching trackers is one admin field.
+ The admin refuses Jira until `.env` has the address and account, and never shows the password.
− One more dependency (and its `httpcore`, `h11`). If httpx dies, `requests` plus `responses`
  replaces it inside `apps/tracker/jira.py` alone.
− Tasks already linked stay keys of the old tracker after a switch; moving them is part of
  writing the next adapter.
