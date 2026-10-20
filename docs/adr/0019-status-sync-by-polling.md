# 0019. Status sync by polling, forward only

Status: Accepted, 2026-10-20

## Context

Clients follow their requests by status; the team moves the linked Jira task (ADR 0010). Jira
8.13 can send webhooks, but only to a public address, and a webhook sent while dimond restarts
is lost. Jira's own workflow goes backwards too (QA sends a task back from staging), and the PM
sets some statuses by hand in dimond (Needs your input, Later, Declined).

## Decision

- A `sync` container runs `sync_tracker --every 900` (ADR 0004): every 15 minutes, with no
  setting. No scheduler library: Django's management command and a loop are enough at
  one short query per 15 minutes.
- Each run reads every linked task of the open requests, plus requests closed in the last 30
  days, in searches of 50 keys. No "updated since" filter: a release date change does not
  touch a task's `updated`, and Jira reads times in that filter in the account's time zone.
- One mapping table for every project (`JIRA_STATUSES`). An unknown status changes nothing and
  is flagged in the log.
- The client's status only moves forward: Planned → In progress → Ready to check → Done. Done
  closes the request (and with it its public links, ADR 0018) for good. Statuses set by people
  win: Declined and Withdrawn are never touched, Needs your input waits for the client, Later
  moves only once the task is in work.
- Every run is logged (`SyncRun`, with a `SyncEvent` per change). Runs with nothing to say are
  kept 7 days, the rest a year.

## Consequences

+ Nothing to install or monitor besides one more container; the sync works on a laptop with no
public address. A webhook (#18) can later trigger an early run without changing the rules.
+ A task bouncing in QA never makes the client's status jump back and forth.
− A change reaches the client up to 15 minutes late until the webhook lands.
− A request whose task really went back stays a step ahead for the client; the log shows it.
− If the `sync` container stops, statuses freeze quietly; the "Jira synced N min ago" line on
the Logs page (#16c) is how the PM notices.
