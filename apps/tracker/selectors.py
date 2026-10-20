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

from .base import TrackerNotConfigured
from .connection import current_tracker
from .models import SyncEvent, SyncOutcome, SyncRun

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
class SyncState:
    """The sync line in the PM's sidebar and the box on the Logs page."""

    # "j.shakuro.com", "Demo tracker", or why there is none.
    tracker: str
    connected: bool
    last: SyncRun | None

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


def sync_log(pm: User, show: RunFilter) -> list[LoggedRun]:
    """The latest finished runs, each with this PM's events in it, filtered as asked."""
    if not has_clients_page(pm):
        return []
    runs = SyncRun.objects.filter(outcome__in=DONE_RUNS).prefetch_related(
        Prefetch("events", queryset=_own_events(pm), to_attr="own_events")
    )
    if show == RunFilter.CHANGES:
        runs = runs.filter(events__in=_own_events(pm)).distinct()
    elif show == RunFilter.PROBLEMS:
        runs = runs.filter(
            Q(outcome=SyncOutcome.FAILED)
            | Q(events__in=_own_events(pm).filter(needs_attention=True))
        ).distinct()
    return [LoggedRun(run, run.own_events) for run in runs[:RUNS_SHOWN]]


def problem_runs(pm: User) -> int:
    """For the Problems chip: how many of the listed runs have something wrong."""
    return sum(1 for run in sync_log(pm, RunFilter.PROBLEMS))


def _tracker_label() -> tuple[str, bool]:
    try:
        tracker = current_tracker()
    except TrackerNotConfigured:
        return "Jira is not set up in .env", False
    if tracker.name == "Demo":
        return "Demo tracker", True
    return urlsplit(tracker.address).hostname or tracker.address, True


def sync_state() -> SyncState:
    tracker, connected = _tracker_label()
    return SyncState(
        tracker=tracker,
        connected=connected,
        last=SyncRun.objects.filter(outcome__in=DONE_RUNS).first(),
    )
