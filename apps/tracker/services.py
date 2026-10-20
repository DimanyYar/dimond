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
    ReleasesFollowed,
    TaskFollowed,
    TaskLost,
    drop_deleted_task,
    follow_moved_task,
    follow_releases,
    follow_task,
)

from .base import (
    Issue,
    IssueNotFound,
    Tracker,
    TrackerCaptcha,
    TrackerError,
    TrackerSignInFailed,
    TrackerUnreachable,
)
from .connection import current_tracker, login_fingerprint
from .models import SyncEvent, SyncOutcome, SyncRun, TrackerKind, TrackerSettings

# How often the worker looks for a Sync now between its regular runs.
QUEUE_CHECK_SECONDS = 3
# A run takes seconds. One still "running" after this was cut off: the worker stopped mid-run.
STUCK_AFTER = timedelta(minutes=10)
# Jira did not answer: one more try this soon, then back to the usual interval.
RETRY_SECONDS = 120
STILL_PAUSED = (
    "Still paused: the login in .env has not changed since Jira rejected it, so dimond did not "
    "try again. Another wrong try brings Jira closer to locking the account."
)

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


def _pause(reason: str, captcha: bool) -> None:
    """Jira rejected the login. Every further try with the same password counts as another
    wrong one, and Jira Server then asks the account for a captcha, which breaks it for
    people too: so the scheduled runs stop at once (PRD: Jira sync)."""
    TrackerSettings.load()
    TrackerSettings.objects.filter(pk=1).update(
        sync_paused_at=timezone.now(),
        sync_paused_reason=reason,
        sync_paused_captcha=captcha,
        sync_paused_login=login_fingerprint(),
    )


def _resume() -> None:
    TrackerSettings.objects.filter(pk=1).exclude(sync_paused_at=None).update(
        sync_paused_at=None, sync_paused_reason="", sync_paused_captcha=False, sync_paused_login=""
    )


def sync_paused() -> bool:
    """Whether the scheduled runs wait. The pause lifts by itself once the Jira login in .env
    is not the one Jira rejected, or once another tracker is chosen; Sync now always tries."""
    row = TrackerSettings.load()
    if row.sync_paused_at is None:
        return False
    if row.tracker != TrackerKind.JIRA or row.sync_paused_login != login_fingerprint():
        _resume()
        return False
    return True


def _login_known_wrong() -> bool:
    """Paused for a wrong password that nobody changed yet: trying it again, even from Sync now,
    is one more wrong password for Jira to count. After a captcha the password may be right,
    and Sync now is how the PM tries once they signed in to Jira in a browser."""
    return sync_paused() and not TrackerSettings.load().sync_paused_captcha


def seconds_until_next(run: SyncRun, every: int, retried: bool) -> tuple[int, bool]:
    """When the worker's next scheduled run is due after `run`, and whether that one is the
    retry. Jira not answering gets one retry two minutes later; a rejected login none: the
    sync is paused."""
    if run.jira_silent and not retried and not sync_paused():
        return RETRY_SECONDS, True
    return every, False


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
    if _login_known_wrong():
        return _finish(run, SyncOutcome.FAILED, error=STILL_PAUSED)
    try:
        tracker = current_tracker()
        if not linked:
            # Nothing to read, but the login is still worth knowing: it is what lifts a pause.
            tracker.account()
        issues = tracker.issues([request.jira_key for request in linked])
        by_key = {issue.key: issue for issue in issues}
        for request in linked:
            _sync_one(run, tracker, request, by_key.get(request.jira_key))
    except TrackerSignInFailed as error:
        _pause(str(error), captcha=isinstance(error, TrackerCaptcha))
        return _finish(run, SyncOutcome.FAILED, error=str(error))
    except TrackerError as error:
        silent = isinstance(error, TrackerUnreachable)
        return _finish(run, SyncOutcome.FAILED, error=str(error), jira_silent=silent)
    _resume()
    problems = run.events.filter(needs_attention=True).exists()
    outcome = SyncOutcome.PROBLEMS if problems else SyncOutcome.SUCCEEDED
    return _finish(run, outcome, checked=len(issues))


def _sync_one(run: SyncRun, tracker: Tracker, request: Request, issue: Issue | None) -> None:
    if issue is None:
        if request.status in CLOSED_STATUSES:
            # The request is over: a task gone from Jira afterwards changes nothing.
            return
        lost, found = _find_lost(tracker, request)
        if lost is not None:
            _log_lost(run, lost)
            if lost.needs_attention:
                return
            # Moved within the client's project: follow the task under its new key.
            request = lost.request
        if found is None:
            return
        issue = found
    followed = follow_task(request, issue.status)
    if followed is not None:
        _log(run, followed)
    planned = follow_releases(request, issue.releases)
    if planned is not None:
        _log_releases(run, planned)


def _find_lost(tracker: Tracker, request: Request) -> tuple[TaskLost | None, Issue | None]:
    """The search did not return the task under its key. Asked by key, Jira answers for a
    moved task under its new key, and "not found" for a deleted one; the same key means
    nothing happened to it. (None, None): Jira did not answer this time; the next run asks
    again."""
    try:
        moved = tracker.issue(request.jira_key)
    except IssueNotFound:
        return drop_deleted_task(request), None
    except TrackerSignInFailed:
        # The whole run stops and pauses: another try would be one more wrong password.
        raise
    except TrackerError:
        return None, None
    if moved.key == request.jira_key:
        # Not moved: the search missed it (Jira's search index can lag behind its tasks).
        return None, moved
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


def _log_releases(run: SyncRun, planned: ReleasesFollowed) -> None:
    shown = planned.shown or "Not set yet"
    SyncEvent.objects.create(
        run=run,
        request=planned.request,
        task_key=planned.request.jira_key,
        what=f"Release: {planned.shown}" if planned.shown else "No release date any more",
        effect=f"The client sees “Planned for: {shown}”.",
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


def _finish(
    run: SyncRun,
    outcome: SyncOutcome,
    checked: int = 0,
    error: str = "",
    jira_silent: bool = False,
) -> SyncRun:
    run.outcome, run.checked, run.error, run.jira_silent = outcome, checked, error, jira_silent
    run.finished_at = timezone.now()
    run.save(update_fields=["outcome", "checked", "error", "jira_silent", "finished_at"])
    _forget_old_runs(run.finished_at)
    return run


def _forget_old_runs(now: datetime) -> None:
    SyncRun.objects.filter(started_at__lt=now - KEEP_RUNS).delete()
    SyncRun.objects.filter(
        started_at__lt=now - KEEP_QUIET_RUNS, outcome=SyncOutcome.SUCCEEDED, events__isnull=True
    ).delete()
