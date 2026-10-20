"""The Jira sync: every linked task's status comes back to its request (PRD: Jira sync;
ADR 0010, 0019)."""

from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.selectors import has_clients_page
from apps.requests.models import CLOSED_STATUSES, Request
from apps.requests.services import (
    TaskFollowed,
    TaskLost,
    drop_deleted_task,
    follow_moved_task,
    follow_task,
)

from .base import Issue, IssueNotFound, Tracker, TrackerError
from .connection import current_tracker
from .models import SyncEvent, SyncOutcome, SyncRun

# How often the worker looks for a Sync now between its regular runs.
QUEUE_CHECK_SECONDS = 3
# A run takes seconds. One still "running" after this was cut off: the worker stopped mid-run.
STUCK_AFTER = timedelta(minutes=10)

# Closed requests are still read for a while, so a task reopened soon after shows up in the
# log; reading every request ever closed would grow without end.
CLOSED_WATCH = timedelta(days=30)
# Quiet runs say only "nothing happened": a week of them is enough to see the sync is alive.
KEEP_QUIET_RUNS = timedelta(days=7)
KEEP_RUNS = timedelta(days=365)


class NotAllowedError(Exception):
    pass


@transaction.atomic
def ask_for_sync(pm: User) -> SyncRun:
    """Sync now: queues a run for the worker, which picks it up within seconds. The page does
    not wait for Jira itself. A second press while one is waiting joins it."""
    if not has_clients_page(pm):
        raise NotAllowedError
    waiting = SyncRun.objects.select_for_update().filter(outcome=SyncOutcome.QUEUED)
    return waiting.first() or SyncRun.objects.create(outcome=SyncOutcome.QUEUED, manual=True)


def close_cut_off_runs() -> int:
    """At worker start: a run left "running" was cut off when the worker stopped, so it is
    logged as failed instead of saying "Syncing…" for good."""
    return SyncRun.objects.filter(outcome=SyncOutcome.RUNNING).update(
        outcome=SyncOutcome.FAILED,
        finished_at=timezone.now(),
        error="The sync stopped before it finished: its worker was restarted.",
    )


def next_queued_run() -> SyncRun | None:
    return SyncRun.objects.filter(outcome=SyncOutcome.QUEUED).order_by("started_at").first()


def sync_tasks(queued: SyncRun | None = None) -> SyncRun:
    """One sync run, or the queued one from Sync now. A failure is logged on the run, never
    raised: the next run tries again."""
    if queued is None:
        run = SyncRun.objects.create()
    else:
        run = queued
        run.outcome, run.started_at = SyncOutcome.RUNNING, timezone.now()
        run.save(update_fields=["outcome", "started_at"])
    now = timezone.now()
    # A task moved into another client's project is no longer followed (PRD: Jira sync).
    linked = list(
        Request.objects.exclude(jira_key="")
        .filter(jira_moved_to="")
        .filter(~Q(status__in=CLOSED_STATUSES) | Q(updated_at__gte=now - CLOSED_WATCH))
    )
    try:
        tracker = current_tracker()
        issues = tracker.issues([request.jira_key for request in linked])
    except TrackerError as error:
        return _finish(run, SyncOutcome.FAILED, error=str(error))
    by_key = {issue.key: issue for issue in issues}
    for request in linked:
        _sync_one(run, tracker, request, by_key.get(request.jira_key))
    problems = run.events.filter(needs_attention=True).exists()
    outcome = SyncOutcome.PROBLEMS if problems else SyncOutcome.SUCCEEDED
    return _finish(run, outcome, checked=len(issues))


def _sync_one(run: SyncRun, tracker: Tracker, request: Request, issue: Issue | None) -> None:
    if issue is None:
        if request.status in CLOSED_STATUSES:
            # The request is over: a task gone from Jira afterwards changes nothing.
            return
        lost, moved = _find_lost(tracker, request)
        if lost is None:
            return
        _log_lost(run, lost)
        if lost.needs_attention or moved is None:
            return
        # Moved within the client's project: follow the task under its new key.
        request, issue = lost.request, moved
    followed = follow_task(request, issue.status)
    if followed is not None:
        _log(run, followed)


def _find_lost(tracker: Tracker, request: Request) -> tuple[TaskLost | None, Issue | None]:
    """The search did not return the task under its key. Asked by key, Jira answers for a
    moved task under its new key, and "not found" for a deleted one. (None, None): Jira did
    not answer this time; the next run asks again."""
    try:
        moved = tracker.issue(request.jira_key)
    except IssueNotFound:
        return drop_deleted_task(request), None
    except TrackerError:
        return None, None
    return follow_moved_task(request, moved), moved


def _log_lost(run: SyncRun, lost: TaskLost) -> None:
    SyncEvent.objects.create(
        run=run,
        request=lost.request,
        task_key=lost.task_key,
        what=lost.what,
        effect=lost.effect,
        needs_attention=lost.needs_attention,
    )


def _log(run: SyncRun, followed: TaskFollowed) -> None:
    jira = followed.jira_to
    if followed.unmapped:
        what = f"Unknown status “{jira}”"
    elif followed.jira_from and followed.jira_from != jira:
        what = f"{followed.jira_from} → {jira}"
    else:
        # The first sync of a task, or only the client's side moved (an answered question).
        what = f"Status in Jira: {jira}"
    SyncEvent.objects.create(
        run=run,
        request=followed.request,
        task_key=followed.request.jira_key,
        what=what,
        effect=followed.effect,
        needs_attention=followed.unmapped,
    )


def _finish(run: SyncRun, outcome: SyncOutcome, checked: int = 0, error: str = "") -> SyncRun:
    run.outcome, run.checked, run.error = outcome, checked, error
    run.finished_at = timezone.now()
    run.save(update_fields=["outcome", "checked", "error", "finished_at"])
    _forget_old_runs(run.finished_at)
    return run


def _forget_old_runs(now: datetime) -> None:
    SyncRun.objects.filter(started_at__lt=now - KEEP_RUNS).delete()
    SyncRun.objects.filter(
        started_at__lt=now - KEEP_QUIET_RUNS, outcome=SyncOutcome.SUCCEEDED, events__isnull=True
    ).delete()
