# 0026. dimond keeps a copy of the release tasks of each client's project

Status: Proposed, 2026-11-02

## Context

Until now dimond read from Jira only the tasks linked to requests (ADR 0010, 0019). The
Releases page (#30) shows a whole release: every task in it, linked or not, broken down into
its subtasks. In the Atlas project (FI) only the parent task has a fix version, a story stays
in To Do while its [FE]/[BE]/[QA] subtasks move and its people are on the subtasks. Asking
Jira while the PM opens the page would make it slow (one release is ~30 tasks and ~60
subtasks), and a Jira that is down would leave the page empty.

## Decision

- **Every sync run copies the releases of each client's Jira project** into three tables:
  `TrackerVersion`, `TrackerTask` (subtasks point to their parent) and `TrackerSprint`.
  Jira stays the source of truth: dimond never edits the copy, the next run rewrites it.
- **What is read, only reading:** all versions of the project except archived ones; the
  tasks of open versions and of the last two released; from the Hot Fix bucket only tasks
  closed in the last 30 days; the subtasks of all of them. The Unreleased bucket keeps its
  version but no tasks (hidden, ~180 tasks). **Also the tasks of the open sprints** (active
  and future) of the project's scrum boards, with or without a fix version, for the
  Sprints mode of the page (#30d, added 2026-11-05); a closed sprint is known only from the
  tasks that name it, so no run pages through a board's whole history. Fields: title,
  type, status and its category, assignee, fix versions, sprints, closed date. **Never** descriptions or comments.
- About 6–8 requests per project per run: versions, the scrum boards and their open
  sprints, the Sprint field's id, the tasks, their subtasks by 50 parents.
- A project Jira does not answer for keeps its previous copy; the error is kept on the run
  (`releases_error`) and in the container log, and the status sync of requests goes on.
- Rows are updated in place by Jira's id or key, so what dimond later keeps about a release
  (hidden, its code freeze) stays attached. A task gone from the read set leaves the copy;
  so does a project no client uses any more.
- Only the client's PM will see the copy (#30b). Clients see none of it until #30i.

## Consequences

+ The Releases page reads its own database: fast, and still there while Jira is down.
+ Progress can come from subtasks, not from the parent's status, which FI does not move.
− Titles and assignees of tasks no client asked about now live in dimond's database (and its
  backups). Same server, same access as the requests themselves.
− The copy can be up to 15 minutes old; a webhook makes it sooner, as for statuses.
− Hot Fix and Unreleased are known by these exact names for now; a per-client setting comes
  when another client names its buckets differently.
