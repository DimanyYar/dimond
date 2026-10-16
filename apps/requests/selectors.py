from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import NamedTuple

from django.db import models
from django.db.models import Count, Max, Prefetch, Q, QuerySet
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.files.models import Attachment

from .models import (
    ASK_TO_WITHDRAW_STATUSES,
    BOARD_COLUMNS,
    CLOSED_STATUSES,
    EDITABLE_STATUSES,
    IN_WORK_STATUSES,
    REQUEST_FIELDS,
    BoardColumn,
    ClientStatus,
    Message,
    Request,
    RequestRevision,
    RequestType,
    StatusEvent,
    WithdrawalRequest,
)


class Show(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"
    ALL = "all", "All"


class Period(models.TextChoices):
    """The periods a client counts requests by (PRD: periods and reports)."""

    WEEK = "week", "This week"
    MONTH = "month", "This month"
    THREE_MONTHS = "3months", "3 months"
    ALL = "all", "All time"


def requests_for(user: User) -> QuerySet[Request]:
    """Every request this person may see: clients their own company's, the PM all of them.

    The team only opens attachment links, never requests, so they get nothing here.
    """
    requests = Request.objects.select_related("company", "author")
    if not user.is_active:
        return requests.none()
    if user.role == Role.PM:
        return requests
    if user.role == Role.CLIENT:
        return requests.filter(company=user.company)
    return requests.none()


def request_for(user: User, pk: int) -> Request:
    """One request this person may see. Raises Request.DoesNotExist for anyone else's,
    exactly as for a missing one, so guessing ids reveals nothing."""
    return requests_for(user).get(pk=pk)


def can_create_requests(user: User) -> bool:
    """Only clients send requests, and only while they still have access."""
    return user.role == Role.CLIENT and user.is_active


def has_my_requests_page(user: User) -> bool:
    """ "My requests" is the clients' home; the PM works from the inbox instead."""
    return user.role == Role.CLIENT


def period_start(period: Period, now: datetime) -> datetime | None:
    """When the period began; None for all time. Weeks start on Monday.

    Counted in the server's time zone until people get their own (PRD: PM availability).
    "3 months" is this month and the two before it, so a month never shows half-empty.
    """
    today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == Period.WEEK:
        return today - timedelta(days=today.weekday())
    if period == Period.MONTH:
        return today.replace(day=1)
    if period == Period.THREE_MONTHS:
        months_back = today.year * 12 + today.month - 1 - 2
        return today.replace(year=months_back // 12, month=months_back % 12 + 1, day=1)
    return None


def _created_since(user: User, since: datetime | None) -> QuerySet[Request]:
    requests = requests_for(user)
    return requests if since is None else requests.filter(created_at__gte=since)


def my_requests(
    user: User, *, show: Show, types: Collection[RequestType], since: datetime | None
) -> QuerySet[Request]:
    """The requests on "My requests", newest first. Later counts as open: it may come back."""
    requests = _created_since(user, since)
    if show == Show.OPEN:
        requests = requests.exclude(status__in=CLOSED_STATUSES)
    elif show == Show.CLOSED:
        requests = requests.filter(status__in=CLOSED_STATUSES)
    if types:
        requests = requests.filter(type__in=types)
    return requests


def my_requests_count(
    user: User, *, show: Show, types: Collection[RequestType], period: Period, now: datetime
) -> int:
    """How many requests a filter would show, so the phone filter sheet can say it upfront."""
    return my_requests(user, show=show, types=types, since=period_start(period, now)).count()


def period_summary(user: User, since: datetime | None) -> dict[str, int]:
    """Counts for the summary and the Open / Closed / All switch, ignoring the type filter."""
    summary: dict[str, int] = _created_since(user, since).aggregate(
        received=Count("pk"),
        in_work=Count("pk", filter=Q(status__in=IN_WORK_STATUSES)),
        later=Count("pk", filter=Q(status=ClientStatus.LATER)),
        done=Count("pk", filter=Q(status=ClientStatus.DONE)),
        declined=Count("pk", filter=Q(status=ClientStatus.DECLINED)),
        closed=Count("pk", filter=Q(status__in=CLOSED_STATUSES)),
    )
    summary["open"] = summary["received"] - summary["closed"]
    return summary


def board(requests: Iterable[Request]) -> list[tuple[BoardColumn, list[Request]]]:
    """Requests sorted into board columns. Empty columns are left out to save width."""
    requests = list(requests)
    columns = [
        (column, [r for r in requests if r.status in column.statuses]) for column in BOARD_COLUMNS
    ]
    return [(column, items) for column, items in columns if items]


class MonthGroup(NamedTuple):
    month: date
    requests: list[Request]
    closed: int


def by_month(requests: Iterable[Request]) -> list[MonthGroup]:
    """Requests grouped by the month they were sent, in the order given (newest first)."""
    groups: dict[date, list[Request]] = {}
    for request in requests:
        month = timezone.localtime(request.created_at).date().replace(day=1)
        groups.setdefault(month, []).append(request)
    return [
        MonthGroup(month, rows, sum(r.status in CLOSED_STATUSES for r in rows))
        for month, rows in groups.items()
    ]


NEEDS_YOU_SHOWN = 3


class WaitingRequest(NamedTuple):
    request: Request
    question: Message | None


@dataclass(frozen=True)
class NeedsYou:
    """The "Dimon needs your input" banner on My requests: a few rows and how many more."""

    shown: list[WaitingRequest]
    more: int


def needs_you(user: User) -> NeedsYou:
    """Requests waiting for the client's answer, longest waiting first.

    Ignores the page's filters: a question from Dimon matters even when its request is
    older than the chosen period or of another type.
    """
    waiting = (
        requests_for(user)
        .filter(status=ClientStatus.NEEDS_INPUT)
        .annotate(
            waiting_since=Max(
                "status_events__created_at",
                filter=Q(status_events__to_status=ClientStatus.NEEDS_INPUT),
            )
        )
        .order_by("waiting_since", "pk")
    )
    total = waiting.count()
    shown = [WaitingRequest(r, _open_question(r)) for r in waiting[:NEEDS_YOU_SHOWN]]
    return NeedsYou(shown, total - len(shown))


@dataclass(frozen=True)
class MyRequestsPage:
    requests: list[Request]
    board: list[tuple[BoardColumn, list[Request]]]
    months: list[MonthGroup]
    summary: dict[str, int]
    has_any_request: bool
    needs_you: NeedsYou


def my_requests_page(
    user: User,
    *,
    show: Show,
    types: Collection[RequestType],
    period: Period,
    with_board: bool,
    now: datetime,
) -> MyRequestsPage:
    """Everything "My requests" shows, read in one place.

    Not paginated: a client sends a handful of requests a week, so even "All time" stays
    small. Revisit when a company passes a few hundred requests.
    """
    since = period_start(period, now)
    shown = list(my_requests(user, show=show, types=types, since=since))
    return MyRequestsPage(
        requests=shown,
        board=board(shown) if with_board else [],
        months=[] if with_board else by_month(shown),
        summary=period_summary(user, since),
        has_any_request=requests_for(user).exists(),
        needs_you=needs_you(user),
    )


def can_post_message(user: User, request: Request) -> bool:
    """The client's company and the PM talk in the thread until the request is closed.

    Done and Declined are final: anything new goes into a new request.
    """
    if not user.is_active or request.status in CLOSED_STATUSES:
        return False
    if user.role == Role.PM:
        return True
    return user.role == Role.CLIENT and user.company_id == request.company_id


def can_ask_for_input(user: User, request: Request) -> bool:
    """Only the PM asks, and only before the request is planned: later questions come from
    the team in Jira (PRD: statuses and Jira mapping)."""
    return (
        user.is_active
        and user.role == Role.PM
        and request.status in (ClientStatus.RECEIVED, ClientStatus.NEEDS_INPUT)
    )


def can_edit_request(user: User, request: Request) -> bool:
    """Anyone at the client's company rewrites the request until it is planned. After that the
    team works from the Jira task, so news goes into the thread instead. The PM never edits
    the client's words: their own version of the task lives in Jira (ADR 0010)."""
    return _is_company_client(user, request) and request.status in EDITABLE_STATUSES


def _is_company_client(user: User, request: Request) -> bool:
    return user.is_active and user.role == Role.CLIENT and user.company_id == request.company_id


def _pending_withdrawal(request: Request) -> WithdrawalRequest | None:
    """The client's ask to withdraw that the PM has not decided yet. Private like
    `_open_question`: it takes a request the caller has already read through `requests_for`.

    Only while the request is open: if the sync closes it meanwhile, there is nothing left
    to decide.
    """
    if request.status in CLOSED_STATUSES:
        return None
    return (
        request.withdrawal_requests.filter(confirmed__isnull=True)
        .select_related("requested_by", "reason")
        .first()
    )


def can_withdraw_now(user: User, request: Request) -> bool:
    """Before it is planned the client withdraws on the spot: nobody works on it yet."""
    return _is_company_client(user, request) and request.status in EDITABLE_STATUSES


def can_ask_to_withdraw(user: User, request: Request) -> bool:
    """From Planned on the client asks with a reason, once at a time, and the PM decides."""
    return (
        _is_company_client(user, request)
        and request.status in ASK_TO_WITHDRAW_STATUSES
        and _pending_withdrawal(request) is None
    )


def can_decide_withdrawal(user: User, request: Request) -> bool:
    return user.is_active and user.role == Role.PM and _pending_withdrawal(request) is not None


# What the thread says when the status changes, in the client's words.
STATUS_EVENT_TEXT = {
    ClientStatus.RECEIVED: "Request received",
    ClientStatus.NEEDS_INPUT: "Waiting for your input",
    ClientStatus.PLANNED: "Planned",
    ClientStatus.IN_PROGRESS: "In progress — the team is working on it",
    ClientStatus.READY_TO_CHECK: "Ready to check",
    ClientStatus.DONE: "Done",
    ClientStatus.DECLINED: "Declined",
    ClientStatus.LATER: "Moved to Later",
    ClientStatus.WITHDRAWN: "Withdrawn",
}


@dataclass(frozen=True)
class ThreadEntry:
    """One line of the thread: a message, a status change or an edit, with its text."""

    at: datetime
    message: Message | None = None
    event: str = ""
    status: str = ""  # empty for an edit: it does not change the status


FIELD_LABELS = {
    "type": "Type",
    "title": "Title",
    "description": "Description",
    "steps": "Steps to reproduce",
    "why": "Why it matters",
    "when_needed": "When needed",
    "link": "Link",
}


class Change(NamedTuple):
    field: str
    old: str
    new: str


@dataclass(frozen=True)
class Edit:
    """One edit of the request: who, when and which fields went from what to what."""

    editor: User
    at: datetime
    changes: list[Change]
    added: list[str]
    removed: list[str]


def _shown(version: Request | RequestRevision, field: str) -> str:
    if field in ("type", "when_needed"):
        return str(getattr(version, f"get_{field}_display")())
    return str(getattr(version, field))


def edits(request: Request) -> list[Edit]:
    """The request's edits, oldest first. Each revision holds the text before an edit, so
    what it was changed to is the next revision, or the request itself for the latest."""
    revisions = list(
        request.revisions.select_related("editor").prefetch_related("added_files", "removed_files")
    )
    if not revisions:
        return []
    after: list[Request | RequestRevision] = [*revisions[1:], request]
    return [
        Edit(
            editor=before.editor,
            at=before.created_at,
            changes=[
                Change(FIELD_LABELS[field], _shown(before, field), _shown(new, field))
                for field in REQUEST_FIELDS
                if getattr(before, field) != getattr(new, field)
            ],
            added=[file.name for file in before.added_files.all()],
            removed=[file.name for file in before.removed_files.all()],
        )
        for before, new in zip(revisions, after, strict=True)
    ]


def _event_entry(event: StatusEvent) -> ThreadEntry:
    text = STATUS_EVENT_TEXT[ClientStatus(event.to_status)]
    if event.to_status == ClientStatus.RECEIVED and event.from_status:
        text = "Back with Dimon"
    return ThreadEntry(at=event.created_at, event=text, status=event.to_status)


def thread(request: Request, request_edits: list[Edit]) -> list[ThreadEntry]:
    """Messages, status changes and edits, oldest first, as one conversation."""
    messages = [
        ThreadEntry(at=m.created_at, message=m)
        for m in request.messages.select_related("author", "author__company").prefetch_related(
            Prefetch("attachments", queryset=Attachment.objects.shown())
        )
    ]
    events = [_event_entry(e) for e in request.status_events.all()]
    # An edit gets a line too, so the PM notices that the request they read has changed.
    edited = [ThreadEntry(at=e.at, event=f"{e.editor} edited the request") for e in request_edits]
    withdrawals = []
    for ask in request.withdrawal_requests.select_related("requested_by"):
        withdrawals.append(
            ThreadEntry(at=ask.created_at, event=f"{ask.requested_by} asked to withdraw it")
        )
        # A confirmed ask shows up as the "Withdrawn" status change instead.
        if ask.confirmed is False and ask.decided_at is not None:
            withdrawals.append(ThreadEntry(at=ask.decided_at, event="Dimon kept the request going"))
    # A message comes before a status change made at the same moment: the PM's question
    # first, then "Waiting for your input".
    return sorted(
        messages + events + edited + withdrawals,
        key=lambda entry: (entry.at, entry.message is None),
    )


PROGRESS_STEPS = (
    ClientStatus.RECEIVED,
    ClientStatus.PLANNED,
    ClientStatus.IN_PROGRESS,
    ClientStatus.READY_TO_CHECK,
    ClientStatus.DONE,
)


class ProgressStep(NamedTuple):
    name: str
    state: str  # "done", "current", "waiting" (current, but waits for the client) or "next"


@dataclass(frozen=True)
class Progress:
    """The way from Received to Done. Later and Declined are off that way, so they get a
    sentence instead of steps."""

    steps: list[ProgressStep]
    position: int  # 1-based number of the current step
    since: datetime
    off_track: str = ""


def progress(request: Request, since: datetime) -> Progress:
    """Where the request is now. `since` is when it got its current status."""
    if request.status == ClientStatus.LATER:
        return Progress(
            [],
            0,
            since,
            "Accepted and parked for later. It comes back to the "
            "plan when there’s room — you’ll see it move.",
        )
    if request.status == ClientStatus.DECLINED:
        return Progress([], 0, since, "Dimon declined this request. The reason is in the thread.")
    if request.status == ClientStatus.WITHDRAWN:
        return Progress([], 0, since, "Withdrawn by the client. Any reason given is in the thread.")
    waiting = request.status == ClientStatus.NEEDS_INPUT
    current = PROGRESS_STEPS.index(ClientStatus.RECEIVED if waiting else request.status)
    steps = []
    for i, status in enumerate(PROGRESS_STEPS):
        name = ClientStatus(status).label
        if i < current:
            state = "done"
        elif i > current:
            state = "next"
        elif waiting:
            state, name = "waiting", f"{name} · needs your input"
        else:
            state = "current"
        steps.append(ProgressStep(name, state))
    return Progress(steps, current + 1, since)


def _open_question(request: Request) -> Message | None:
    """The PM's question the client is asked to answer, while the request waits for them.

    The PM's latest question, not their latest message: a "thanks" or a nudge written
    while waiting does not replace what was asked. Private: it takes a request the caller
    has already read through `requests_for`, so it never widens what someone may see.
    """
    if request.status != ClientStatus.NEEDS_INPUT:
        return None
    return request.messages.filter(asks_for_input=True).last()


@dataclass(frozen=True)
class RequestPage:
    viewer: User
    request: Request
    # The request's own files, shown under its text; `all_files` adds those from the thread.
    files: list[Attachment]
    all_files: list[Attachment]
    # Images and PDFs, in the same order: the ones the viewer goes through.
    viewable_files: list[Attachment]
    thread: list[ThreadEntry]
    edits: list[Edit]
    progress: Progress
    question: Message | None
    can_post: bool
    can_ask: bool
    can_edit: bool
    withdrawal: WithdrawalRequest | None
    can_withdraw_now: bool
    can_ask_to_withdraw: bool
    can_decide_withdrawal: bool


def request_page(user: User, pk: int) -> RequestPage:
    """Everything the request card shows. Raises Request.DoesNotExist like `request_for`."""
    request = request_for(user, pk)
    request_edits = edits(request)
    entries = thread(request, request_edits)
    all_files = list(request.attachments.shown().select_related("uploaded_by"))
    # Every request has at least its "Received" event, so this is never empty.
    since = max(entry.at for entry in entries if entry.status)
    return RequestPage(
        viewer=user,
        request=request,
        files=[file for file in all_files if file.message_id is None],
        all_files=all_files,
        viewable_files=[file for file in all_files if file.shown_in_browser],
        thread=entries,
        edits=request_edits,
        progress=progress(request, since),
        question=_open_question(request),
        can_post=can_post_message(user, request),
        can_ask=can_ask_for_input(user, request),
        can_edit=can_edit_request(user, request),
        withdrawal=_pending_withdrawal(request),
        can_withdraw_now=can_withdraw_now(user, request),
        can_ask_to_withdraw=can_ask_to_withdraw(user, request),
        can_decide_withdrawal=can_decide_withdrawal(user, request),
    )


@dataclass(frozen=True)
class EditPage:
    request: Request
    question: Message | None
    files: list[Attachment]


def edit_page(user: User, pk: int) -> EditPage:
    """The request to edit and the PM's open question, if any: editing does not answer it.

    Raises Request.DoesNotExist like `request_for`; whether it may be edited now is
    `can_edit_request`'s call.
    """
    request = request_for(user, pk)
    return EditPage(
        request=request,
        question=_open_question(request),
        files=list(request.attachments.shown().filter(message__isnull=True)),
    )
