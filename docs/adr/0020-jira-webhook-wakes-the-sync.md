# 0020. A Jira webhook only wakes the sync

Status: Accepted, 2026-10-21

## Context

Polling every 15 minutes (ADR 0019) means a client may wait that long to see a new status.
Jira 8.13 can call an address when a task changes, but its webhooks:

- carry no signature or secret (the secret field arrived in later Data Center releases), so
  anyone who knows the address can call it with any body;
- are sent synchronously: Jira waits for the answer before it finishes saving the task, so a
  slow receiver slows down everyone editing tasks in the filtered projects;
- are never retried: one sent while dimond restarts is lost.

One webhook serves the whole Jira, which already sends dozens to other tools on every edit.

## Decision

- `POST /jira/webhook/<token>/?key=${issue.key}` wakes the sync. The token comes from
  `TRACKER_JIRA_WEBHOOK_TOKEN` in `.env` and is compared in constant time; a wrong token, or
  none set, answers "not found". An empty token turns the webhook off.
- The body is never read; the webhook is set up with "Exclude body". Only the task key in the
  address is used, and only to decide whether to wake the sync: a key that is not the task of
  a followed request (another Shakuro project, a request closed long ago) does nothing.
- Waking means queueing a full run, the same as Sync now. The run reads every linked task from
  Jira itself, so a forged call can at most cause one extra run. Webhooks arriving while a run
  waits join it; a bulk edit of 40 tasks makes one or two runs. The receiver never calls Jira
  and answers within milliseconds.
- While the sync is paused for a rejected login, webhooks are ignored: a run would only try the
  password Jira rejected again.
- In Jira the webhook listens to issue updated, issue deleted, comment created and comment
  updated, with a JQL filter on the clients' projects. Version events are not used: they cannot
  be filtered and would arrive for every release in Jira; release dates keep arriving with the
  15-minute poll.
- Polling stays the source of truth (ADR 0010, 0019) and catches anything a webhook missed.
- The Logs page names the tasks that woke a run ("webhook for FI-2214, then a full check");
  a webhook run that found nothing is not listed, since Jira calls on every edit. It still
  counts as the last sync.
- A run that brings a status change or a team question no webhook announced logs "Webhook
  missed this change" as a problem (added Oct 21, 2026): the webhook in Jira is off, its JQL
  filter leaves the project out, or dimond was down when Jira called. A first status of a newly
  linked task, a moved or deleted task and release dates never count. Nothing is blamed while
  the webhook token is empty, with the demo tracker, or right after a failed run, which may
  have used up the webhook calls without reading Jira.

## Consequences

+ Statuses and team questions reach the client within seconds; nothing new to install.
+ A leaked token gives nothing but extra runs; rotating it is a change in `.env` and in Jira.
− The token sits in the webhook address, which every Jira administrator can read, and in web
  server access logs unless the production proxy drops that path (#21).
− A new client project needs adding to the webhook's JQL filter in Jira by hand; until then its
  tasks still arrive with the poll, and the log flags each change as missed.
− Webhooks only work once dimond has a public address (#21); on a laptop they are tried by hand
  with curl.
