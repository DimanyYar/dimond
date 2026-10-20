"""What the PM reads about the Jira sync: the Logs page and the sync line in the sidebar
(PRD: Jira sync; mockup 4.x · PM logs). A PM sees only events about their own clients
(ADR 0014); whole-run news, such as Jira not answering, is everyone's."""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from urllib.parse import urlsplit

from django.db.models import Prefetch, Q, QuerySet
from django.utils import timezone
from django.utils.timesince import timesince

from apps.accounts.models import User
from apps.accounts.selectors import has_clients_page
from apps.requests.models import CLOSED_STATUSES, JIRA_STATUSES, ClientStatus, Request

from .base import TrackerNotConfigured
from .connection import current_tracker
from .models import SyncEvent, SyncOutcome, SyncRun
from .services import STUCK_AFTER

RUNS_SHOWN = 50
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
        return f"{self.run.checked} linked task{'s' if self.run.checked != 1 else ''} checked"

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
    return LogsPage(
        runs=[LoggedRun(item.run, item.events, open=item is first_with_events) for item in listed],
        problem_runs=_runs(pm, RunFilter.PROBLEMS).count(),
        attention=needs_attention(pm),
        mapping=list(JIRA_STATUSES.items()),
    )


def _runs(pm: User, show: RunFilter) -> QuerySet[SyncRun]:
    """Finished runs, newest first, that the filter keeps for this PM."""
    runs = SyncRun.objects.filter(outcome__in=DONE_RUNS)
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
    return SyncState(
        tracker=tracker,
        connected=connected,
        last=SyncRun.objects.filter(outcome__in=DONE_RUNS).first(),
        waiting=SyncRun.objects.filter(
            Q(outcome=SyncOutcome.QUEUED)
            | Q(outcome=SyncOutcome.RUNNING, started_at__gte=timezone.now() - STUCK_AFTER)
        ).exists(),
        attention=len(needs_attention(pm)),
    )
