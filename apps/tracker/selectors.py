"""What the PM reads about the Jira sync: the Logs page and the sync line in the sidebar
(PRD: Jira sync; mockup 4.x · PM logs), and the Releases page built on the copy of each
client's releases (#30; ADR 0026). A PM sees only their own clients (ADR 0014); whole-run
news, such as Jira not answering, is everyone's."""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from urllib.parse import urlsplit

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Prefetch, Q, QuerySet
from django.utils import timezone
from django.utils.timesince import timesince

from apps.accounts.models import Company, User
from apps.accounts.selectors import clients_for, has_clients_page
from apps.requests.models import CLOSED_STATUSES, JIRA_STATUSES, ClientStatus, Request

from .base import TrackerNotConfigured
from .connection import current_tracker
from .models import (
    SyncEvent,
    SyncOutcome,
    SyncRun,
    TrackerKind,
    TrackerSettings,
    TrackerTask,
    TrackerVersion,
)
from .services import HIDDEN_BUCKET, RECENT_BUCKET, RELEASED_READ, STUCK_AFTER

RUNS_SHOWN = 50
# Tasks named in a webhook run's subtitle; a bulk edit names the first ones and counts the rest.
KEYS_NAMED = 2
DONE_RUNS = (SyncOutcome.SUCCEEDED, SyncOutcome.PROBLEMS, SyncOutcome.FAILED)


class RunFilter(StrEnum):
    ALL = "all"
    CHANGES = "changes"
    PROBLEMS = "problems"


@dataclass(frozen=True)
class LoggedRun:
    run: SyncRun
    events: list[SyncEvent]
    # The newest run that changed something opens by itself: that is what the PM came for.
    open: bool = False
    # The tasks of this PM's clients whose webhook woke the run; other PMs' stay unnamed.
    webhook_keys: tuple[str, ...] = ()

    @property
    def trigger(self) -> str:
        """What started the run, as the page says it: manual, webhook or auto."""
        if self.run.manual:
            return "manual"
        return "webhook" if self.run.webhook_keys else "auto"

    @property
    def problems(self) -> int:
        return sum(event.needs_attention for event in self.events) + self.failed

    @property
    def failed(self) -> bool:
        return self.run.outcome == SyncOutcome.FAILED

    @property
    def result(self) -> str:
        """The run's icon on the page: ok, warn or fail."""
        if self.failed:
            return "fail"
        return "warn" if self.problems else "ok"

    @property
    def title(self) -> str:
        if self.failed:
            return "Sync failed"
        changes = len(self.events) - self.problems
        parts = []
        if changes:
            parts.append(f"{changes} change{'s' if changes != 1 else ''}")
        if self.problems:
            parts.append(f"{self.problems} problem{'s' if self.problems != 1 else ''}")
        return ", ".join(parts) or "No changes"

    @property
    def subtitle(self) -> str:
        if self.failed:
            return self.run.error
        if self.problems:
            return "Sync finished, problems need you"
        if self.trigger == "webhook":
            return f"{self._webhook_for()}, then a full check"
        return f"{self.run.checked} linked task{'s' if self.run.checked != 1 else ''} checked"

    def _webhook_for(self) -> str:
        if not self.webhook_keys:
            return "webhook"
        named = ", ".join(self.webhook_keys[:KEYS_NAMED])
        more = len(self.webhook_keys) - KEYS_NAMED
        return f"webhook for {named} and {more} more" if more > 0 else f"webhook for {named}"

    @property
    def seconds(self) -> float:
        assert self.run.finished_at is not None  # only finished runs are listed
        return (self.run.finished_at - self.run.started_at).total_seconds()


@dataclass(frozen=True)
class Attention:
    title: str
    detail: str
    request: Request
    # A task Jira moved into another client's project: Link another task or Unlink.
    moved: bool = False


@dataclass(frozen=True)
class SyncState:
    """The sync line in the PM's sidebar and the box on the Logs page."""

    # "j.shakuro.com", "Demo tracker", or why there is none.
    tracker: str
    connected: bool
    last: SyncRun | None
    waiting: bool
    attention: int
    # Why the sync is paused (Jira rejected the login), or empty.
    paused: str
    # Paused for a captcha: the fix is signing in to Jira in a browser, not .env.
    captcha: bool

    @property
    def failed(self) -> bool:
        return self.last is not None and self.last.outcome == SyncOutcome.FAILED

    @property
    def last_ago(self) -> str:
        """ "just now" or "4 minutes ago", for the sidebar line."""
        assert self.last is not None  # the line says "not synced yet" without a run
        if timezone.now() - self.last.started_at < timedelta(minutes=1):
            return "just now"
        return f"{timesince(self.last.started_at)} ago"


def _own_events(pm: User) -> QuerySet[SyncEvent]:
    return SyncEvent.objects.filter(request__company__owner=pm).select_related(
        "request", "request__company"
    )


@dataclass(frozen=True)
class LogsPage:
    runs: list[LoggedRun]
    problem_runs: int
    attention: list[Attention]
    mapping: list[tuple[str, ClientStatus]]


def logs_page(pm: User, show: RunFilter) -> LogsPage:
    """The Logs page: the latest runs as filtered, how many runs have problems (the Problems
    chip counts them all, not only the ones listed), what needs attention, the mapping."""
    if not has_clients_page(pm):
        return LogsPage(runs=[], problem_runs=0, attention=[], mapping=[])
    listed = [
        LoggedRun(run, run.own_events)
        for run in _runs(pm, show).prefetch_related(
            Prefetch("events", queryset=_own_events(pm), to_attr="own_events")
        )[:RUNS_SHOWN]
    ]
    first_with_events = next((item for item in listed if item.events), None)
    own_keys = set(
        Request.objects.filter(
            company__owner=pm,
            jira_key__in=[key for item in listed for key in item.run.webhook_keys],
        ).values_list("jira_key", flat=True)
    )
    return LogsPage(
        runs=[
            LoggedRun(
                item.run,
                item.events,
                open=item is first_with_events,
                webhook_keys=tuple(key for key in item.run.webhook_keys if key in own_keys),
            )
            for item in listed
        ],
        problem_runs=_runs(pm, RunFilter.PROBLEMS).count(),
        attention=needs_attention(pm),
        mapping=list(JIRA_STATUSES.items()),
    )


def _runs(pm: User, show: RunFilter) -> QuerySet[SyncRun]:
    """Finished runs, newest first, that the filter keeps for this PM. A webhook run that found
    nothing is left out: Jira calls on every edit of a task, and those runs say nothing new."""
    runs = SyncRun.objects.filter(outcome__in=DONE_RUNS).exclude(
        Q(manual=False, outcome=SyncOutcome.SUCCEEDED, events__isnull=True) & ~Q(webhook_keys=[])
    )
    if show == RunFilter.CHANGES:
        return runs.filter(events__in=_own_events(pm)).distinct()
    if show == RunFilter.PROBLEMS:
        return runs.filter(
            Q(outcome=SyncOutcome.FAILED)
            | Q(events__in=_own_events(pm).filter(needs_attention=True))
        ).distinct()
    return runs


def needs_attention(pm: User) -> list[Attention]:
    """Problems that are still there now, not just logged once, on open requests of this PM's
    clients: a task deleted or moved to another client's project, an unmapped status."""
    if not has_clients_page(pm):
        return []
    open_requests = (
        Request.objects.filter(company__owner=pm)
        .exclude(status__in=CLOSED_STATUSES)
        .select_related("company")
    )
    items = []
    for request in open_requests.filter(jira_key="").exclude(lost_jira_key=""):
        items.append(
            Attention(
                title=f"{request.lost_jira_key} not found in Jira",
                detail=f"{request.key} · {request.company}. Deleted, so dimond unlinked it; "
                f"the client still sees “{ClientStatus(request.status).label}”. Link another "
                "task or decline the request.",
                request=request,
            )
        )
    for request in open_requests.exclude(jira_moved_to=""):
        own_project = request.jira_moved_to.split("-")[0] == request.company.jira_project
        why = "That key belongs to another request" if own_project else "That’s another project"
        items.append(
            Attention(
                title=f"{request.jira_key} moved to {request.jira_moved_to}",
                detail=f"{request.key} · {request.company}. {why}, so the sync stopped "
                f"following it; the client still sees “{ClientStatus(request.status).label}”.",
                request=request,
                moved=True,
            )
        )
    unmapped = (
        open_requests.exclude(jira_key="")
        .filter(jira_moved_to="")
        .exclude(jira_status="")
        .exclude(jira_status__in=JIRA_STATUSES)
        .order_by("jira_key")
    )
    for request in unmapped:
        items.append(
            Attention(
                title=f"“{request.jira_status}” isn’t mapped",
                detail=f"{request.jira_key} sits in it; {request.company} keeps seeing "
                f"“{ClientStatus(request.status).label}”.",
                request=request,
            )
        )
    return items


def _tracker_label() -> tuple[str, bool]:
    try:
        tracker = current_tracker()
    except TrackerNotConfigured:
        return "Jira is not set up in .env", False
    if tracker.name == "Demo":
        return "Demo tracker", True
    return urlsplit(tracker.address).hostname or tracker.address, True


def sync_state(pm: User) -> SyncState:
    tracker, connected = _tracker_label()
    row = TrackerSettings.load()
    paused = _paused_reason(row)
    return SyncState(
        tracker=tracker,
        connected=connected,
        last=SyncRun.objects.filter(outcome__in=DONE_RUNS).first(),
        waiting=SyncRun.objects.filter(
            Q(outcome=SyncOutcome.QUEUED)
            | Q(outcome=SyncOutcome.RUNNING, started_at__gte=timezone.now() - STUCK_AFTER)
        ).exists(),
        attention=len(needs_attention(pm)),
        paused=paused,
        captcha=bool(paused) and row.sync_paused_captcha,
    )


def _paused_reason(row: TrackerSettings) -> str:
    if row.sync_paused_at is None or row.tracker != TrackerKind.JIRA:
        return ""
    return row.sync_paused_reason


# The Releases page (#30, step 30b; canvas 7 · PMReleaseView). Progress comes from subtasks,
# not the parent's Jira status (D-004): in FI a story stays in To Do while its subtasks move.

SUB_BUG = "Sub-bug"
TRACKS = ("FE", "BE", "QA", "Other", "QA bug")
TRACK_PREFIX = re.compile(r"^\[(FE|BE|QA)\]", re.IGNORECASE)
VERSION_NUMBER = re.compile(r"[0-9]+(\.[0-9]+)*")


class ReleaseGroup(StrEnum):
    OPEN = "Open"
    HOTFIXES = "Hotfixes"
    RELEASED = "Released"
    BUCKETS = "Buckets"


class Health(StrEnum):
    STALE = "Stale"
    EMPTY = "Empty"
    NO_DATE = "No date"
    RECENT = "Last 30 days"
    RELEASED = "Released"
    NONE = ""


def track_of(subtask: TrackerTask) -> str:
    """Which part of the team a subtask belongs to: its [FE]/[BE]/[QA] prefix, or "QA bug" for
    a bug found in testing."""
    if subtask.issue_type == SUB_BUG:
        return "QA bug"
    found = TRACK_PREFIX.match(subtask.summary)
    return found.group(1).upper() if found else "Other"


def _is_done(task: TrackerTask) -> bool:
    subtasks = list(task.subtasks.all())
    if not subtasks:
        return task.status_category == "done"
    return all(sub.status_category == "done" for sub in subtasks)


def _is_started(task: TrackerTask) -> bool:
    return any(t.status_category != "new" for t in [task, *task.subtasks.all()])


@dataclass(frozen=True)
class Track:
    name: str
    total: int
    done: int
    in_progress: int

    @property
    def done_pct(self) -> int:
        return round(self.done * 100 / self.total)

    @property
    def progress_pct(self) -> int:
        return round(self.in_progress * 100 / self.total)


@dataclass(frozen=True)
class Release:
    version: TrackerVersion
    group: ReleaseGroup
    hidden: bool
    tasks: list[TrackerTask]
    # Subtasks with a fix version of their own whose parent is in another release: their work
    # counts in this release's tracks, but they are not tasks of it.
    other_subtasks: list[TrackerTask]
    # Tasks are not read for the Unreleased bucket (D-008), so there is nothing to count.
    tasks_read: bool
    start: date | None
    # The previous release when it has no date yet: this one starts the day after it ships.
    starts_after: str
    stale: bool
    client_requests: int
    jira_url: str

    @property
    def subtasks(self) -> list[TrackerTask]:
        found = {sub.pk: sub for task in self.tasks for sub in task.subtasks.all()}
        found.update((sub.pk, sub) for sub in self.other_subtasks)
        return list(found.values())

    @property
    def done(self) -> int:
        return sum(_is_done(task) for task in self.tasks)

    @property
    def in_progress(self) -> int:
        return sum(_is_started(task) and not _is_done(task) for task in self.tasks)

    @property
    def done_pct(self) -> int:
        return round(self.done * 100 / len(self.tasks)) if self.tasks else 0

    @property
    def health(self) -> Health:
        if self.group == ReleaseGroup.RELEASED:
            return Health.RELEASED
        if self.group == ReleaseGroup.HOTFIXES:
            return Health.RECENT
        if self.group == ReleaseGroup.BUCKETS:
            return Health.NONE
        if self.stale:
            return Health.STALE
        if not self.tasks:
            return Health.EMPTY
        return Health.NONE if self.version.release_date else Health.NO_DATE

    @property
    def date_line(self) -> str:
        version = self.version
        if self.group == ReleaseGroup.RELEASED:
            return f"Released {_day(version.release_date)}"
        if self.group == ReleaseGroup.HOTFIXES:
            return "Done in the last 30 days · a Jira bucket, not a release"
        if self.group == ReleaseGroup.BUCKETS:
            return "Jira bucket · dimond doesn’t read its tasks"
        if self.stale:
            return f"{self.done} of {len(self.tasks)} done, never released"
        if self.start:
            starts = f"Starts {_day(self.start)}"
        elif self.starts_after:
            starts = f"Starts the day after {self.starts_after} ships"
        else:
            starts = "No start date"
        ships = (
            f"ships {_day(version.release_date)}" if version.release_date else "no release date yet"
        )
        return f"{starts} · {ships}"

    @property
    def tracks(self) -> list[Track]:
        by_track: dict[str, list[TrackerTask]] = {}
        for sub in self.subtasks:
            by_track.setdefault(track_of(sub), []).append(sub)
        return [
            Track(
                name,
                len(subs),
                sum(s.status_category == "done" for s in subs),
                sum(s.status_category == "indeterminate" for s in subs),
            )
            for name in TRACKS
            if (subs := by_track.get(name))
        ]

    @property
    def warnings(self) -> list[str]:
        """What the PM should look at in an open release (canvas 7 · PMReleaseView)."""
        if self.group != ReleaseGroup.OPEN:
            return []
        if self.stale:
            left = len(self.tasks) - self.done
            return [
                f"Looks forgotten: {self.done} of {len(self.tasks)} done, {left} not done. "
                "Release it or archive it in Jira, and move what isn’t done."
            ]
        found = []
        if self.tasks and not self.version.release_date:
            if self.client_requests:
                found.append(
                    f"No release date in Jira: {_count(self.client_requests, 'client request')} "
                    "in it show no date for this release."
                )
            else:
                found.append("No release date in Jira.")
        unplanned = [t for t in self.tasks if t.issue_type == "Story" and not t.subtasks.all()]
        if len(unplanned) == 1:
            found.append(
                f"“{unplanned[0].summary}” has no subtasks yet: nobody has planned the work."
            )
        elif unplanned:
            found.append(
                f"{len(unplanned)} stories have no subtasks yet: nobody has planned the work."
            )
        moving = [t for t in self.tasks if _is_started(t) and not _is_done(t)]
        qa = [s for t in moving for s in t.subtasks.all() if track_of(s) == "QA"]
        if qa and all(s.status_category == "new" for s in qa):
            found.append(
                "QA hasn’t started on any task in progress; "
                f"{_count(len(qa), 'QA subtask')} {'is' if len(qa) == 1 else 'are'} To Do."
            )
        return found


def _day(day: date | None) -> str:
    return f"{day:%b} {day.day}" if day else ""


def _count(n: int, thing: str) -> str:
    return f"{n} {thing}{'' if n == 1 else 's'}"


@dataclass(frozen=True)
class ReleasesPage:
    clients: list[Company]
    client: Company | None
    releases: list[Release]
    selected: Release | None

    @property
    def shown(self) -> list[Release]:
        return [r for r in self.releases if not r.hidden]

    @property
    def hidden(self) -> list[Release]:
        return [r for r in self.releases if r.hidden]


def _group(version: TrackerVersion) -> ReleaseGroup:
    if version.name == RECENT_BUCKET:
        return ReleaseGroup.HOTFIXES
    if version.name == HIDDEN_BUCKET:
        return ReleaseGroup.BUCKETS
    return ReleaseGroup.RELEASED if version.released else ReleaseGroup.OPEN


def _jira_address() -> str:
    try:
        return current_tracker().address
    except TrackerNotConfigured:
        return ""


def _start(version: TrackerVersion, previous: TrackerVersion | None) -> tuple[date | None, str]:
    """When an open release starts: Jira's start date, else the day after the previous release
    (D-006); if that one has no date yet either, its name for "the day after … ships"."""
    if version.start_date or not previous:
        return version.start_date, ""
    if previous.release_date:
        return previous.release_date + timedelta(days=1), ""
    return None, previous.name


def _version_number(version: TrackerVersion) -> tuple[int, ...] | None:
    """A version name such as 26.7.5 as numbers to compare; None for a name like "Q3 web"."""
    if not VERSION_NUMBER.fullmatch(version.name):
        return None
    return tuple(int(part) for part in version.name.split("."))


def _is_stale(version: TrackerVersion, versions: list[TrackerVersion]) -> bool:
    """A release that looks forgotten: never marked released, though one with a higher number
    already is, or its release date has passed. Numbers, not Jira's order: in FI hotfix
    releases such as 26.7.5 are added after 26.8.0, and that one is not forgotten."""
    number = _version_number(version)
    passed = number is not None and any(
        _group(v) == ReleaseGroup.RELEASED
        and (other := _version_number(v)) is not None
        and other > number
        for v in versions
    )
    overdue = bool(version.release_date and version.release_date < timezone.localdate())
    return passed or overdue


def _is_hidden(version: TrackerVersion) -> bool:
    try:
        return version.visibility.hidden
    except ObjectDoesNotExist:
        return _group(version) == ReleaseGroup.BUCKETS


def _tasks_by_version(
    project_key: str,
) -> tuple[dict[int, list[TrackerTask]], dict[int, list[TrackerTask]]]:
    """Each release's tasks, and the subtasks that sit in it while their parent does not."""
    tasks = TrackerTask.objects.filter(project_key=project_key).prefetch_related(
        "subtasks", "versions"
    )
    by_version: dict[int, list[TrackerTask]] = {}
    subtasks_only: dict[int, list[TrackerTask]] = {}
    parents_in: dict[int, set[int]] = {}
    for task in tasks:
        if task.parent_id is None:
            for version in task.versions.all():
                by_version.setdefault(version.pk, []).append(task)
                parents_in.setdefault(task.pk, set()).add(version.pk)
    for task in tasks:
        if task.parent_id is not None:
            for version in task.versions.all():
                if version.pk not in parents_in.get(task.parent_id, set()):
                    subtasks_only.setdefault(version.pk, []).append(task)
    return by_version, subtasks_only


def _releases(client: Company) -> list[Release]:
    versions = list(
        TrackerVersion.objects.filter(project_key=client.jira_project).select_related("visibility")
    )
    by_version, subtasks_only = _tasks_by_version(client.jira_project)
    requests = set(client.requests.exclude(jira_key="").values_list("jira_key", flat=True))
    address = _jira_address()
    # Only the newest released ones have their tasks copied (D-008); older ones are history.
    released = sorted(
        (v for v in versions if _group(v) == ReleaseGroup.RELEASED and v.release_date),
        key=lambda v: v.release_date or date.min,
        reverse=True,
    )[:RELEASED_READ]
    releases = []
    previous: TrackerVersion | None = None
    for version in versions:
        group = _group(version)
        # The Unreleased bucket's tasks are not read (D-008): the few that also sit in a read
        # release would pass for the whole bucket.
        read = group != ReleaseGroup.BUCKETS
        own = by_version.get(version.pk, []) if read else []
        if group != ReleaseGroup.RELEASED or version in released:
            start, starts_after = (
                _start(version, previous) if group == ReleaseGroup.OPEN else (None, "")
            )
            releases.append(
                Release(
                    version=version,
                    group=group,
                    hidden=_is_hidden(version),
                    tasks=own,
                    other_subtasks=subtasks_only.get(version.pk, []) if read else [],
                    tasks_read=read,
                    start=start,
                    starts_after=starts_after,
                    stale=group == ReleaseGroup.OPEN and bool(own) and _is_stale(version, versions),
                    client_requests=sum(task.key in requests for task in own),
                    jira_url=f"{address}/projects/{version.project_key}/versions/{version.jira_id}"
                    if address
                    else "",
                )
            )
        if group in (ReleaseGroup.OPEN, ReleaseGroup.RELEASED):
            previous = version
    order = list(ReleaseGroup)
    return sorted(
        releases,
        key=lambda r: (
            order.index(r.group),
            # Newest release first; the rest in Jira's own order.
            -(r.version.release_date or date.min).toordinal()
            if r.group == ReleaseGroup.RELEASED
            else r.version.position,
        ),
    )


def releases_page(pm: User, client_pk: int | None, version_pk: int | None) -> ReleasesPage:
    """The Releases page of one of this PM's clients (canvas 7 · PMReleaseView): the list on
    the left and the chosen release. Another PM's client or release is the same as none."""
    clients = list(clients_for(pm).exclude(jira_project=""))
    client = next((c for c in clients if c.pk == client_pk), clients[0] if clients else None)
    releases = _releases(client) if client else []
    shown = [r for r in releases if not r.hidden]
    selected = next(
        (r for r in releases if r.version.pk == version_pk),
        next((r for r in shown if r.group == ReleaseGroup.OPEN), shown[0] if shown else None),
    )
    return ReleasesPage(clients, client, releases, selected)


def release_for(pm: User, version_pk: int) -> TrackerVersion:
    """A release of one of this PM's clients. Raises TrackerVersion.DoesNotExist for anyone
    else's, exactly as for a missing one."""
    projects = clients_for(pm).exclude(jira_project="").values_list("jira_project", flat=True)
    return TrackerVersion.objects.get(pk=version_pk, project_key__in=projects)
