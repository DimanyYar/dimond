"""The Jira sync: every linked task's status comes back to its request (PRD: Jira sync;
ADR 0010, 0019)."""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.accounts.models import Company, User
from apps.accounts.selectors import has_clients_page
from apps.files.uploads import MAX_VIDEO_SIZE, CheckedFile, check_upload
from apps.notifications.selectors import update_recipients
from apps.requests.models import CLOSED_STATUSES, ClientStatus, Message, Request
from apps.requests.services import (
    QuestionFollowed,
    ReleasesFollowed,
    TaskFollowed,
    TaskLost,
    add_team_question,
    drop_deleted_task,
    edit_team_question,
    follow_moved_task,
    follow_releases,
    follow_task,
    keep_team_markup,
)

from .base import (
    Comment,
    Issue,
    IssueNotFound,
    ProjectNotFound,
    Release,
    ReleaseTask,
    Sprint,
    Tracker,
    TrackerCaptcha,
    TrackerError,
    TrackerForbidden,
    TrackerRefused,
    TrackerSignInFailed,
    TrackerUnreachable,
    Version,
)
from .connection import current_tracker, login_fingerprint
from .models import (
    ReleaseMove,
    ReleaseVisibility,
    SyncEvent,
    SyncOutcome,
    SyncRun,
    TrackerKind,
    TrackerSettings,
    TrackerSprint,
    TrackerTask,
    TrackerVersion,
)

logger = logging.getLogger(__name__)

# How often the worker looks for a queued run (Sync now, a webhook) between its regular runs.
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
# The start of a team question quoted in the log; the thread has the rest.
QUOTE_LENGTH = 120
# Hot Fix and Unreleased are buckets, not releases (D-004 in the project notes): Hot Fix shows
# only what was closed lately; Unreleased is hidden, so its tasks are not read at all.
RECENT_BUCKET = "Hot Fix"
HIDDEN_BUCKET = "Unreleased"
RECENT_BUCKET_DAYS = 30
# Released versions read with their tasks, newest first: older ones are history.
RELEASED_READ = 2
# A Jira task key as Jira writes it: project key, dash, number (FI-2214).
TASK_KEY = re.compile(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*")


class NotAllowedError(Exception):
    pass


@transaction.atomic
def ask_for_sync(pm: User) -> SyncRun:
    """Sync now: queues a run for the worker, which picks it up within seconds. The page does
    not wait for Jira itself. A second press while one is waiting joins it."""
    if not has_clients_page(pm):
        raise NotAllowedError
    _lock_queue()
    run = SyncRun.objects.filter(outcome=SyncOutcome.QUEUED).first()
    if run is None:
        return SyncRun.objects.create(outcome=SyncOutcome.QUEUED, manual=True)
    if not run.manual:
        # A webhook queued it first; the log should still say the PM asked too.
        run.manual = True
        run.save(update_fields=["manual"])
    return run


@transaction.atomic
def wake_for_webhook(task_key: str) -> SyncRun | None:
    """A Jira webhook said `task_key` changed: queue a run so the client sees it in seconds,
    not in up to 15 minutes (PRD: Jira sync; ADR 0020). The run reads every linked task from
    Jira itself, so nothing the webhook says is trusted. Webhooks that arrive while a run
    waits join it: a bulk edit of 40 tasks makes one or two runs, not 40.

    None: nothing to do. The key is not a task of a followed request (another Shakuro project,
    a request closed long ago), or the sync is paused, where a run would only try the login
    Jira rejected again."""
    if _lock_queue().tracker != TrackerKind.JIRA or sync_paused():
        return None
    if not followed_requests().filter(jira_key=task_key).exists():
        return None
    run = SyncRun.objects.filter(outcome=SyncOutcome.QUEUED).first()
    if run is None:
        return SyncRun.objects.create(outcome=SyncOutcome.QUEUED, webhook_keys=[task_key])
    if task_key not in run.webhook_keys:
        run.webhook_keys.append(task_key)
        run.save(update_fields=["webhook_keys"])
    return run


def _lock_queue() -> TrackerSettings:
    """Locks the settings row until the transaction ends. Sync now and webhooks both take it
    before looking for a waiting run, so pressed or sent at the same moment they join one run
    instead of each queueing their own: a run that does not exist yet cannot be locked."""
    TrackerSettings.load()
    return TrackerSettings.objects.select_for_update().get(pk=1)


def followed_requests() -> QuerySet[Request]:
    """The requests whose tasks every run reads: open ones, plus those closed in the last 30
    days. A task moved into another client's project is no longer followed (PRD: Jira sync)."""
    return (
        Request.objects.exclude(jira_key="")
        .filter(jira_moved_to="")
        .filter(~Q(status__in=CLOSED_STATUSES) | Q(updated_at__gte=timezone.now() - CLOSED_WATCH))
    )


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
    """One sync run, or the queued one from Sync now or a webhook. A failure is logged on the
    run, never raised: the next run tries again."""
    if queued is None:
        run = SyncRun.objects.create()
    else:
        run = queued
        run.outcome, run.started_at = SyncOutcome.RUNNING, timezone.now()
        run.save(update_fields=["outcome", "started_at"])
    linked = list(followed_requests())
    webhooks_trusted = _webhooks_trusted(run)
    if _login_known_wrong():
        return _finish(run, SyncOutcome.FAILED, error=STILL_PAUSED)
    try:
        tracker = current_tracker()
        if not linked:
            # Nothing to read, but the login is still worth knowing: it is what lifts a pause.
            tracker.account()
        issues = tracker.issues([request.jira_key for request in linked])
        by_key = {issue.key: issue for issue in issues}
        changed = [r for r in linked if _sync_one(run, tracker, r, by_key.get(r.jira_key))]
        run.releases_copied, run.releases_error = copy_releases(tracker)
    except TrackerSignInFailed as error:
        _pause(str(error), captcha=isinstance(error, TrackerCaptcha))
        return _finish(run, SyncOutcome.FAILED, error=str(error))
    except TrackerError as error:
        silent = isinstance(error, TrackerUnreachable)
        return _finish(run, SyncOutcome.FAILED, error=str(error), jira_silent=silent)
    _resume()
    if webhooks_trusted:
        _flag_missed_webhooks(run, changed)
    problems = run.events.filter(needs_attention=True).exists()
    outcome = SyncOutcome.PROBLEMS if problems else SyncOutcome.SUCCEEDED
    return _finish(run, outcome, checked=len(issues))


def _sync_one(run: SyncRun, tracker: Tracker, request: Request, issue: Issue | None) -> bool:
    """Brings one task's news to its request. True: the team changed the task's status or asked
    the client something, the news a Jira webhook announces (ADR 0020)."""
    moved = False
    if issue is None:
        if request.status in CLOSED_STATUSES:
            # The request is over: a task gone from Jira afterwards changes nothing.
            return False
        lost, found = _find_lost(tracker, request)
        if lost is not None:
            _log_lost(run, lost)
            if lost.needs_attention:
                return False
            # Moved within the client's project: follow the task under its new key.
            request, moved = lost.request, True
        if found is None:
            return False
        issue = found
    followed = follow_task(request, issue.status)
    if followed is not None:
        _log(run, followed)
    planned = follow_releases(request, issue.releases)
    if planned is not None:
        _log_releases(run, planned)
    asked = _follow_questions(run, tracker, request, issue)
    # The first status of a newly linked task is no news from Jira; a move, a deletion or a
    # release date may well reach dimond without a webhook, so they never count as missed.
    status_moved = followed is not None and followed.jira_from not in ("", followed.jira_to)
    return not moved and (status_moved or asked)


def copy_releases(tracker: Tracker) -> tuple[int, str]:
    """Copies the releases of every client's Jira project for the Releases page (#30; ADR
    0026): versions, their tasks and subtasks, linked to requests or not. Only reads Jira. A
    project Jira does not answer for keeps its previous copy; the others go on. Returns how
    many tasks were copied and what failed."""
    projects = set(Company.objects.exclude(jira_project="").values_list("jira_project", flat=True))
    copied, failures, open_sprints = 0, [], []
    for project_key in sorted(projects):
        try:
            count, sprints = _copy_project(tracker, project_key)
            copied += count
            open_sprints += sprints
        except TrackerSignInFailed:
            raise
        except TrackerError as error:
            failures.append(f"{project_key}: {error}")
            logger.warning("Releases of %s not copied: %s", project_key, error)
    # A project no client uses any more.
    TrackerTask.objects.exclude(project_key__in=projects).delete()
    TrackerVersion.objects.exclude(project_key__in=projects).delete()
    # A closed sprint no copied task names any more; open ones stay listed even when empty or
    # unread, so the Sprints mode can name them. After a failed project its open sprints are
    # unknown this run, so nothing is pruned until a run reads every project.
    if not failures:
        TrackerSprint.objects.filter(tasks__isnull=True).exclude(jira_id__in=open_sprints).delete()
    return copied, " ".join(failures)


def _copy_project(tracker: Tracker, project_key: str) -> tuple[int, list[int]]:
    """Jira is read whole first, so a failure halfway leaves the previous copy as it was.
    Returns how many tasks were copied and the ids of the project's open sprints."""
    versions = [v for v in tracker.versions(project_key) if not v.archived]
    try:
        open_sprints = tracker.open_sprints(project_key)
    except ProjectNotFound:
        # The project exists (its versions were just read): Jira has no Agile boards for it,
        # e.g. without Jira Software. Releases are copied without sprints.
        open_sprints = []
    released = sorted(
        (v for v in versions if v.released and v.release_date),
        key=lambda v: v.release_date or date.min,
        reverse=True,
    )
    tasks = tracker.release_tasks(
        project_key,
        [v.id for v in versions if not v.released and v.name not in (RECENT_BUCKET, HIDDEN_BUCKET)]
        + [v.id for v in released[:RELEASED_READ]],
        [v.id for v in versions if v.name == RECENT_BUCKET],
        timezone.localdate() - timedelta(days=RECENT_BUCKET_DAYS),
        sorted(_sprints_read(open_sprints)),
    )
    _save_copy(project_key, versions, tasks, open_sprints)
    return len(tasks), [s.id for s in open_sprints]


def _sprints_read(open_sprints: list[Sprint]) -> set[int]:
    """The sprints whose tasks are copied, with or without a release: the active ones and the
    next one of each board. Later future sprints are often buckets that never close (FI has
    ~1,000 tasks in them), so they are only named (Tony, Nov 5)."""
    read = {s.id for s in open_sprints if s.state == "active"}
    boards_done: set[int | str] = set()
    for sprint in open_sprints:
        # A board without an id (an older Jira) is told apart by its name.
        board = sprint.board_id or sprint.board
        if sprint.state == "future" and board not in boards_done:
            read.add(sprint.id)
            boards_done.add(board)
    return read


@transaction.atomic
def _save_copy(
    project_key: str, versions: list[Version], tasks: list[ReleaseTask], open_sprints: list[Sprint]
) -> None:
    """Rows are updated in place, never dropped and made again, so anything dimond later keeps
    about a release or a task stays attached to it."""
    TrackerVersion.objects.bulk_create(
        [
            TrackerVersion(
                project_key=project_key,
                jira_id=v.id,
                name=v.name,
                start_date=v.start_date,
                release_date=v.release_date,
                released=v.released,
                position=position,
            )
            for position, v in enumerate(versions)
        ],
        update_conflicts=True,
        unique_fields=["jira_id"],
        update_fields=_copied_fields(TrackerVersion, "jira_id"),
    )
    TrackerVersion.objects.filter(project_key=project_key).exclude(
        jira_id__in=[v.id for v in versions]
    ).delete()
    boards = {s.board_id: s.board for s in open_sprints}
    # A closed sprint of a board with no open sprints left keeps the board an earlier run saw.
    known_boards = dict(TrackerSprint.objects.exclude(board="").values_list("jira_id", "board"))
    sprints = {
        s.id: TrackerSprint(
            project_key=project_key,
            jira_id=s.id,
            name=s.name,
            board=boards.get(s.board_id or 0) or known_boards.get(s.id, ""),
            state=s.state,
            starts=s.starts,
            ends=s.ends,
        )
        for task in tasks
        for s in task.sprints
    }
    # A sprint's own answer from its board is newer than what a task remembers of it.
    read = _sprints_read(open_sprints)
    sprints |= {
        s.id: TrackerSprint(
            project_key=project_key,
            jira_id=s.id,
            name=s.name,
            board=s.board,
            state=s.state,
            starts=s.starts,
            ends=s.ends,
            tasks_read=s.id in read,
        )
        for s in open_sprints
    }
    TrackerSprint.objects.bulk_create(
        list(sprints.values()),
        update_conflicts=True,
        unique_fields=["jira_id"],
        update_fields=["project_key", "name", "board", "state", "starts", "ends", "tasks_read"],
    )
    TrackerTask.objects.bulk_create(
        [
            TrackerTask(
                project_key=project_key,
                key=t.key,
                summary=t.summary,
                issue_type=t.issue_type,
                status=t.status,
                status_category=t.status_category,
                assignee_login=t.assignee_login,
                assignee_name=t.assignee_name,
                resolved_at=t.resolved,
            )
            for t in tasks
        ],
        update_conflicts=True,
        unique_fields=["key"],
        update_fields=_copied_fields(TrackerTask, "key"),
    )
    TrackerTask.objects.filter(project_key=project_key).exclude(
        key__in=[t.key for t in tasks]
    ).delete()
    _link_copy(project_key, tasks, list(sprints))


def _copied_fields(model: type[TrackerTask | TrackerVersion], key: str) -> list[str]:
    """Every column Jira fills, for rows already there; the parent is linked afterwards."""
    return [f.name for f in model._meta.concrete_fields if f.name not in ("id", key, "parent")]


def _link_copy(project_key: str, tasks: list[ReleaseTask], sprint_ids: list[int]) -> None:
    task_pk = dict(TrackerTask.objects.filter(project_key=project_key).values_list("key", "pk"))
    version_pk = dict(
        TrackerVersion.objects.filter(project_key=project_key).values_list("jira_id", "pk")
    )
    sprint_pk = dict(
        TrackerSprint.objects.filter(jira_id__in=sprint_ids).values_list("jira_id", "pk")
    )
    # A subtask whose parent is in no release read stands on its own.
    TrackerTask.objects.bulk_update(
        [TrackerTask(pk=task_pk[t.key], parent_id=task_pk.get(t.parent_key)) for t in tasks],
        ["parent"],
    )
    in_version = TrackerTask.versions.through
    in_version.objects.filter(trackertask__project_key=project_key).delete()
    in_version.objects.bulk_create(
        in_version(trackertask_id=task_pk[t.key], trackerversion_id=version_pk[v])
        for t in tasks
        for v in t.version_ids
        # An archived version is not copied.
        if v in version_pk
    )
    in_sprint = TrackerTask.sprints.through
    in_sprint.objects.filter(trackertask__project_key=project_key).delete()
    in_sprint.objects.bulk_create(
        in_sprint(trackertask_id=task_pk[t.key], trackersprint_id=sprint_pk[s.id])
        for t in tasks
        for s in t.sprints
    )


def _webhooks_trusted(run: SyncRun) -> bool:
    """Whether this run may blame the webhook for news it finds. Only with the webhook on and
    Jira chosen, and only after a run that succeeded: a failed or paused run before it may
    have used up the webhook calls without reading Jira."""
    if not settings.TRACKER_JIRA_WEBHOOK_TOKEN:
        return False
    if TrackerSettings.load().tracker != TrackerKind.JIRA:
        return False
    finished = (SyncOutcome.SUCCEEDED, SyncOutcome.PROBLEMS, SyncOutcome.FAILED)
    previous = (
        SyncRun.objects.filter(outcome__in=finished)
        .exclude(pk=run.pk)
        .order_by("-started_at", "-pk")
        .first()
    )
    return previous is not None and previous.outcome != SyncOutcome.FAILED


@transaction.atomic
def _flag_missed_webhooks(run: SyncRun, changed: list[Request]) -> None:
    """News no webhook announced: the webhook in Jira is off, its JQL filter leaves the project
    out, or dimond was down when Jira called. The client got it up to 15 minutes late, and the
    PM learns it from the log rather than from a client (PRD: Jira sync; ADR 0020)."""
    # Taken like a webhook takes it: one arriving right now either is in a run below or waits.
    _lock_queue()
    run.refresh_from_db(fields=["webhook_keys"])
    announced = set(run.webhook_keys)
    # A webhook that came while this run read Jira woke the next run instead: not missed.
    for waiting in SyncRun.objects.filter(outcome=SyncOutcome.QUEUED):
        announced.update(waiting.webhook_keys)
    for request in changed:
        if request.jira_key in announced:
            continue
        project = request.jira_key.split("-")[0]
        SyncEvent.objects.create(
            run=run,
            request=request,
            task_key=request.jira_key,
            what="Webhook missed this change",
            effect=f"Jira did not call dimond about {request.jira_key}, so the client saw it up "
            f"to 15 minutes late. Is the webhook in Jira on, and does its JQL include {project}?",
            needs_attention=True,
        )


def _follow_questions(run: SyncRun, tracker: Tracker, request: Request, issue: Issue) -> bool:
    """Comments tagged #client become questions in the request's thread, once each; an edit
    in Jira rewords the question (PRD: Jira sync, team questions). True: any was new or
    edited."""
    known = {
        comment_id: (edited_at, markup)
        for comment_id, edited_at, markup in Message.objects.filter(request=request)
        .exclude(team_comment_id="")
        .values_list("team_comment_id", "team_edited_at", "team_markup")
    }
    asked = [c for c in issue.comments if c.asks_the_client]
    # A task Jira moved between clients' projects keeps its comments: a question already shown
    # to another request's client was meant for them, never for this one.
    elsewhere = set(
        Message.objects.filter(team_comment_id__in=[c.id for c in asked])
        .exclude(request=request)
        .values_list("team_comment_id", flat=True)
    )
    news = False
    for comment in asked:
        if comment.id in elsewhere:
            continue
        if comment.id in known:
            edited_at, markup = known[comment.id]
            if edited_at != comment.updated:
                _log_question(run, edit_team_question(request, comment))
                news = True
            elif not markup and comment.markup:
                keep_team_markup(request, comment)
            continue
        files, not_shown = _question_files(tracker, comment)
        _log_question(run, add_team_question(request, comment, files, not_shown))
        news = True
    return news


def _question_files(tracker: Tracker, comment: Comment) -> tuple[list[CheckedFile], list[str]]:
    """The question's files, checked like a client's upload, and the names of those the client
    will not see. Jira not answering stops the run before the question is saved, so the next
    run brings it whole."""
    files: list[CheckedFile] = []
    not_shown = list(comment.missing_files)
    for file in comment.files:
        if file.size > MAX_VIDEO_SIZE:
            not_shown.append(file.name)
            continue
        try:
            content = tracker.file_content(file)
            files.append(check_upload(SimpleUploadedFile(file.name, content, file.content_type)))
        except (TrackerRefused, TrackerForbidden, ValidationError):
            not_shown.append(file.name)
    return files, not_shown


def _log_question(run: SyncRun, followed: QuestionFollowed) -> None:
    request, message = followed.request, followed.message
    if followed.edited:
        what, effect = "Question edited in Jira", "The client reads the new wording."
    else:
        what = "Question for the client · #client"
        before = followed.status_from
        if before == ClientStatus.NEEDS_INPUT:
            moved = "it already waited for an answer"
        else:
            moved = f"{before.label} → Needs your input"
        quote = (
            message.text if len(message.text) <= QUOTE_LENGTH else message.text[:QUOTE_LENGTH] + "…"
        )
        effect = (
            f"{message.team_author} asked: “{quote}” Shown to {request.company} in "
            f"{request.key}; {moved}.{_who_gets_an_email(request)}"
        )
        if message.files_not_shown:
            effect += f" Not shown to the client: {', '.join(message.files_not_shown)}."
    SyncEvent.objects.create(
        run=run,
        request=request,
        task_key=request.jira_key,
        what=what,
        effect=_fit(effect, SyncEvent._meta.get_field("effect").max_length),
    )


def _fit(text: str, limit: int | None) -> str:
    if limit is None or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


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
        effect=_fit(
            followed.effect
            + (
                _who_gets_an_email(followed.request)
                if followed.status_from != followed.status_to
                else ""
            ),
            300,
        ),
        needs_attention=followed.unmapped,
    )


def _who_gets_an_email(request: Request) -> str:
    """ " Anna K. and Mark T. will get an email." after news the client hears about, so the PM
    sees who. The email itself leaves a few minutes later (PRD: email notifications)."""
    names = [person.name or person.email for person in update_recipients(request)]
    if not names:
        return ""
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f" {listed} will get an email."


def _finish(
    run: SyncRun,
    outcome: SyncOutcome,
    checked: int = 0,
    error: str = "",
    jira_silent: bool = False,
) -> SyncRun:
    run.outcome, run.checked, run.error, run.jira_silent = outcome, checked, error, jira_silent
    run.finished_at = timezone.now()
    run.save(
        update_fields=[
            "outcome",
            "checked",
            "error",
            "jira_silent",
            "finished_at",
            "releases_copied",
            "releases_error",
        ]
    )
    _forget_old_runs(run.finished_at)
    return run


def _forget_old_runs(now: datetime) -> None:
    SyncRun.objects.filter(started_at__lt=now - KEEP_RUNS).delete()
    SyncRun.objects.filter(
        started_at__lt=now - KEEP_QUIET_RUNS, outcome=SyncOutcome.SUCCEEDED, events__isnull=True
    ).delete()


def set_release_hidden(version: TrackerVersion, hidden: bool) -> None:
    """Hides a release from the PM's Releases list or brings it back (D-006 in the project
    notes). Only dimond's list changes; Jira and clients see nothing. The caller has already
    checked the release is one of this PM's clients' (selectors.release_for)."""
    ReleaseVisibility.objects.update_or_create(version=version, defaults={"hidden": hidden})


# Moving a task to another release from the Releases page (#30, 30f; D-005).


@dataclass(frozen=True)
class Moved:
    move: ReleaseMove
    # Where the task is now; None for no release.
    target: TrackerVersion | None
    # Subtasks Jira would not move, though the task itself moved.
    left_behind: list[str]


def move_task(
    pm: User,
    task: TrackerTask,
    source: TrackerVersion | None,
    target: TrackerVersion | None,
    *,
    undo_of: ReleaseMove | None = None,
) -> Moved:
    """The PM moves a task from one release to another, or to none. dimond changes the fix
    version in Jira first, on the task and on each subtask that has the same one of its own
    (in FI subtasks usually have none), then its own copy, so the page shows the move at once.
    A linked request gets its new "Planned for" and a line in its thread, never an email
    (D-005); taking a task out of every dated release changes the card quietly. Raises the
    tracker's error when Jira refuses the task itself: then nothing has changed."""
    tracker = current_tracker()
    source_id = source.jira_id if source else ""
    target_id = target.jira_id if target else ""
    tracker.move_fix_version(task.key, source_id, target_id)
    _swap_version(task, source, target)
    if undo_of is not None:
        subtasks = list(TrackerTask.objects.filter(key__in=undo_of.subtask_keys))
    else:
        subtasks = [s for s in task.subtasks.all() if source and source in s.versions.all()]
    moved, left_behind = [], []
    for subtask in subtasks:
        try:
            tracker.move_fix_version(subtask.key, source_id, target_id)
        except TrackerError as error:
            logger.warning("Subtask %s not moved with %s: %s", subtask.key, task.key, error)
            left_behind.append(subtask.key)
            continue
        _swap_version(subtask, source, target)
        moved.append(subtask.key)
    move = ReleaseMove.objects.create(
        moved_by=pm,
        project_key=task.project_key,
        task_key=task.key,
        task_summary=task.summary,
        from_version_id=source_id,
        from_name=source.name if source else "",
        to_version_id=target_id,
        to_name=target.name if target else "",
        subtask_keys=moved,
        undo_of=undo_of,
    )
    _follow_moved_request(task)
    return Moved(move, target, left_behind)


def undo_move(pm: User, move: ReleaseMove) -> Moved:
    """Undo on the toast: the task goes back where it was, with the subtasks that moved along.
    Raises TrackerVersion.DoesNotExist when a release has since gone from the copy, and
    TrackerTask.DoesNotExist when the task has."""
    task = TrackerTask.objects.get(key=move.task_key)
    back_from = (
        TrackerVersion.objects.get(jira_id=move.to_version_id) if move.to_version_id else None
    )
    back_to = (
        TrackerVersion.objects.get(jira_id=move.from_version_id) if move.from_version_id else None
    )
    return move_task(pm, task, back_from, back_to, undo_of=move)


def _swap_version(
    task: TrackerTask, source: TrackerVersion | None, target: TrackerVersion | None
) -> None:
    if source is not None:
        task.versions.remove(source)
    if target is not None:
        task.versions.add(target)


def _follow_moved_request(task: TrackerTask) -> None:
    request = Request.objects.filter(jira_key=task.key).first()
    if request is None:
        return
    dated = sorted(
        (Release(v.name, v.release_date) for v in task.versions.all() if v.release_date),
        key=lambda release: release.release_date,
    )
    follow_releases(request, tuple(dated))
