import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import NamedTuple

from django.db import models
from django.db.models import (
    Case,
    Count,
    Exists,
    F,
    Max,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.accounts.models import Company, Role, User
from apps.files.models import Attachment

from .models import (
    ASK_TO_WITHDRAW_STATUSES,
    BOARD_COLUMNS,
    CLOSED_STATUSES,
    EDITABLE_STATUSES,
    IN_WORK_STATUSES,
    NEEDS_YOU_COLUMN,
    QUEUED_COLUMN,
    REQUEST_FIELDS,
    BoardColumn,
    ClientStatus,
    InternalNote,
    Message,
    Request,
    RequestRevision,
    RequestType,
    RequestVisit,
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
    """Every request this person may see: clients their own company's, a PM those of the
    clients they own (PRD: every PM has their own clients).

    The team only opens attachment links, never requests, so they get nothing here.
    """
    requests = Request.objects.select_related("company", "author")
    if not user.is_active:
        return requests.none()
    if user.role == Role.PM:
        return requests.filter(company__owner=user)
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
    """The requests page is home for clients (their company's requests) and for PMs (their
    clients'). The team only opens attachment links."""
    return user.role in (Role.CLIENT, Role.PM)


def is_client(user: User) -> bool:
    """Clients and the PM share pages; this picks the client's words and navigation."""
    return user.role == Role.CLIENT


class PmReason(models.TextChoices):
    """Why a request waits for the PM's move (PRD: "Needs you" is whose move it is, not a
    status). In the order the PM sees them: an ask to withdraw first."""

    WITHDRAW = "withdraw", "Asks to withdraw"
    REPLIED = "replied", "Client replied"
    NEW = "new", "New"


PM_REASON_ORDER = list(PmReason)


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


def _of_company(user: User, company: int | None) -> QuerySet[Request]:
    """The PM's client chips narrow everything on the page to one company. A client is
    always narrowed to their own by `requests_for`, so the chip changes nothing for them."""
    requests = requests_for(user)
    return requests if company is None else requests.filter(company_id=company)


def _created_since(user: User, since: datetime | None, company: int | None) -> QuerySet[Request]:
    requests = _of_company(user, company)
    return requests if since is None else requests.filter(created_at__gte=since)


KEY_PATTERN = re.compile(r"^\s*dmn-?(\d+)\s*$", re.IGNORECASE)


def search(user: User, query: str, company: int | None = None) -> QuerySet[Request]:
    """Requests by their DMN- key or words from the title, whatever their age or status:
    someone searching for DMN-1031 wants it even if it closed months ago. The PM's client
    chip still applies, so results never mix clients while one is chosen."""
    requests = _of_company(user, company)
    key = KEY_PATTERN.match(query)
    if key:
        return requests.filter(pk=int(key.group(1)))
    return requests.filter(title__icontains=query.strip())


def my_requests(
    user: User,
    *,
    show: Show,
    types: Collection[RequestType],
    since: datetime | None,
    company: int | None = None,
) -> QuerySet[Request]:
    """The requests on the page, newest first. Later counts as open: it may come back."""
    requests = _created_since(user, since, company)
    if show == Show.OPEN:
        requests = requests.exclude(status__in=CLOSED_STATUSES)
    elif show == Show.CLOSED:
        requests = requests.filter(status__in=CLOSED_STATUSES)
    if types:
        requests = requests.filter(type__in=types)
    return requests


def with_unread(requests: QuerySet[Request], user: User) -> QuerySet[Request]:
    """Adds `unread`: how many messages from others came after the person last opened each
    request. A request they never opened counts every message from others. Requests the
    person may not see are dropped, whatever the caller passed in."""
    visits = RequestVisit.objects.filter(request=OuterRef("request"), user=user)
    new_messages = (
        Message.objects.filter(request=OuterRef("pk"))
        .exclude(author=user)
        .filter(Q(created_at__gt=Subquery(visits.values("seen_at")[:1])) | ~Exists(visits))
        .order_by()
        .values("request")
        .annotate(n=Count("pk"))
        .values("n")
    )
    visible = requests.filter(pk__in=requests_for(user).values("pk"))
    return visible.annotate(unread=Coalesce(Subquery(new_messages), 0))


def my_requests_count(
    user: User,
    *,
    show: Show,
    types: Collection[RequestType],
    period: Period,
    now: datetime,
    company: int | None = None,
) -> int:
    """How many requests a filter would show, so the phone filter sheet can say it upfront."""
    since = period_start(period, now)
    return my_requests(user, show=show, types=types, since=since, company=company).count()


def with_pm_reason(requests: QuerySet[Request]) -> QuerySet[Request]:
    """Adds `pm_reason`: why an open request waits for the PM, or None.

    An open ask to withdraw comes first; then the client wrote last; then a request the PM
    has not answered yet. Any message from anyone at the client's company counts, since
    clients only ever talk to the PM.
    """
    latest = Message.objects.filter(request=OuterRef("pk")).order_by("-created_at", "-pk")
    last_author_role = Subquery(latest.values("author__role")[:1])
    last_message_at = Subquery(latest.values("created_at")[:1])
    # "No reply needed" covers the client's messages up to that moment, not later ones.
    unhandled = Q(pm_handled_at__isnull=True) | Q(last_message_at__gt=F("pm_handled_at"))
    pending_ask = WithdrawalRequest.objects.filter(request=OuterRef("pk"), confirmed__isnull=True)
    pm_wrote = Message.objects.filter(request=OuterRef("pk"), author__role=Role.PM)
    open_request = ~Q(status__in=CLOSED_STATUSES)
    return requests.alias(last_author=last_author_role, last_message_at=last_message_at).annotate(
        pm_reason=Case(
            # A request the Jira sync closed while an ask was pending has nothing left to decide.
            When(open_request & Exists(pending_ask), then=Value(PmReason.WITHDRAW)),
            When(
                open_request & Q(last_author=Role.CLIENT) & unhandled,
                then=Value(PmReason.REPLIED),
            ),
            # Once the PM handled a request (No reply needed, Later) it is no longer new.
            When(
                Q(status=ClientStatus.RECEIVED, pm_handled_at__isnull=True) & ~Exists(pm_wrote),
                then=Value(PmReason.NEW),
            ),
            default=None,
            output_field=models.CharField(),
        )
    )


def period_summary(
    user: User, since: datetime | None, company: int | None = None
) -> dict[str, int]:
    """Counts for the summary and the Open / Closed / All switch, ignoring the type filter."""
    summary: dict[str, int] = _created_since(user, since, company).aggregate(
        received=Count("pk"),
        in_work=Count("pk", filter=Q(status__in=IN_WORK_STATUSES)),
        later=Count("pk", filter=Q(status=ClientStatus.LATER)),
        done=Count("pk", filter=Q(status=ClientStatus.DONE)),
        declined=Count("pk", filter=Q(status=ClientStatus.DECLINED)),
        closed=Count("pk", filter=Q(status__in=CLOSED_STATUSES)),
    )
    summary["open"] = summary["received"] - summary["closed"]
    return summary


class BoardGroup(NamedTuple):
    column: BoardColumn
    requests: list[Request]
    # Cards name their status where it is not obvious from the column.
    show_status: bool


def pm_reasons(requests: QuerySet[Request]) -> dict[int, PmReason]:
    """Which of these requests wait for the PM, and why."""
    rows = with_pm_reason(requests).filter(Q(pm_reason__isnull=False))
    # The type checker cannot see a field that `with_pm_reason` adds with `annotate`.
    pairs = rows.values_list("pk", "pm_reason")  # type: ignore[misc]
    return {pk: PmReason(reason) for pk, reason in pairs}


def _column_slug(request: Request, reasons: Mapping[int, PmReason] | None) -> str:
    if reasons is not None and request.pk in reasons:
        return NEEDS_YOU_COLUMN
    # "Needs you" on the client's board is the PM's question; for the PM that request waits
    # in line, labelled "Waiting on client".
    if reasons is not None and request.status == ClientStatus.NEEDS_INPUT:
        return QUEUED_COLUMN
    return next(c.slug for c in BOARD_COLUMNS if request.status in c.statuses)


def board(
    requests: Iterable[Request], reasons: Mapping[int, PmReason] | None = None
) -> list[BoardGroup]:
    """Requests sorted into board columns. Empty columns are left out to save width.

    Both roles get the same columns; only "Needs you" differs: the client's holds the PM's
    questions, the PM's holds whatever waits for the PM, whatever its status. `reasons`
    comes from `pm_reasons` and is given for the PM only.
    """
    requests = list(requests)
    for_pm = reasons is not None
    slugs = {r.pk: _column_slug(r, reasons) for r in requests}
    groups = []
    for column in BOARD_COLUMNS:
        items = [r for r in requests if slugs[r.pk] == column.slug]
        if reasons is not None and column.slug == NEEDS_YOU_COLUMN:
            # Asks to withdraw first, as in the banner; newest first within each reason.
            items.sort(key=lambda r: PM_REASON_ORDER.index(reasons[r.pk]))
        if items:
            # The PM's "Needs you" and Queued both hold several statuses.
            mixed = for_pm and column.slug in (NEEDS_YOU_COLUMN, QUEUED_COLUMN)
            groups.append(BoardGroup(column, items, column.mixes_statuses or mixed))
    return groups


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


PM_NEEDS_SHOWN = 3


@dataclass(frozen=True)
class PmNeeds:
    """The PM's "Needs you" banner: a few rows, asks to withdraw first, and how many more."""

    shown: list[tuple[Request, PmReason]]
    more: int


def pm_needs(user: User, company: int | None) -> PmNeeds:
    """What waits for the PM, oldest first within each reason. Ignores the period and type
    filters: a client's reply matters whatever its request's age."""
    requests = _of_company(user, company)
    reasons = pm_reasons(requests)
    waiting = [
        (r, reasons[r.pk]) for r in requests.filter(pk__in=reasons).order_by("created_at", "pk")
    ]
    waiting.sort(key=lambda pair: PM_REASON_ORDER.index(pair[1]))
    return PmNeeds(waiting[:PM_NEEDS_SHOWN], max(len(waiting) - PM_NEEDS_SHOWN, 0))


class ClientChip(NamedTuple):
    company: Company
    needs_pm: int


def client_chips(user: User) -> list[ClientChip]:
    """The PM's client filter: every company with requests, and how many wait for the PM."""
    waiting = Counter(
        requests_for(user)
        .filter(pk__in=pm_reasons(requests_for(user)))
        .values_list("company_id", flat=True)
    )
    companies = Company.objects.filter(pk__in=requests_for(user).values("company")).order_by("name")
    return [ClientChip(company, waiting[company.pk]) for company in companies]


@dataclass(frozen=True)
class MyRequestsPage:
    requests: list[Request]
    board: list[BoardGroup]
    months: list[MonthGroup]
    summary: dict[str, int]
    has_any_request: bool
    needs_you: NeedsYou | None
    pm_needs: PmNeeds | None
    clients: list[ClientChip]


def my_requests_page(
    user: User,
    *,
    show: Show,
    types: Collection[RequestType],
    period: Period,
    with_board: bool,
    now: datetime,
    company: int | None = None,
    query: str = "",
) -> MyRequestsPage:
    """Everything the requests page shows, read in one place.

    Not paginated: a client sends a handful of requests a week, so even "All time" stays
    small. Revisit when the PM's all-clients view passes a few hundred open requests.
    A search ignores the other filters: it looks for one request.
    """
    since = period_start(period, now)
    for_pm = user.role == Role.PM
    if query:
        found = search(user, query, company)
    else:
        found = my_requests(user, show=show, types=types, since=since, company=company)
    found = with_unread(found, user)
    reasons = pm_reasons(found) if for_pm else None
    # The template reads `pm_reason` off each card; the board sorts by `reasons`.
    shown = list(with_pm_reason(found) if for_pm else found)
    return MyRequestsPage(
        requests=shown,
        board=board(shown, reasons) if with_board else [],
        months=[] if with_board else by_month(shown),
        summary=period_summary(user, since, company),
        has_any_request=requests_for(user).exists(),
        needs_you=None if for_pm else needs_you(user),
        pm_needs=pm_needs(user, company) if for_pm else None,
        clients=client_chips(user) if for_pm else [],
    )


def can_post_message(user: User, request: Request) -> bool:
    """The client's company and the PM talk in the thread until the request is closed.

    Done and Declined are final: anything new goes into a new request.
    """
    if request.status in CLOSED_STATUSES:
        return False
    return _is_owner_pm(user, request) or _is_company_client(user, request)


def can_ask_for_input(user: User, request: Request) -> bool:
    """Only the PM asks, and only before the request is planned: later questions come from
    the team in Jira (PRD: statuses and Jira mapping)."""
    return _is_owner_pm(user, request) and request.status in (
        ClientStatus.RECEIVED,
        ClientStatus.NEEDS_INPUT,
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
    return _is_owner_pm(user, request) and _pending_withdrawal(request) is not None


def _is_owner_pm(user: User, request: Request) -> bool:
    """The PM who owns the request's client; any other PM may not even see it."""
    return user.is_active and user.role == Role.PM and request.company.owner_id == user.pk


def can_move_to_later(user: User, request: Request) -> bool:
    """Later is "yes, but not now": for a request the PM has not planned yet (PRD: PM
    buttons). Once planned, the Jira sync moves it there when the task leaves the release."""
    return _is_owner_pm(user, request) and request.status in (
        ClientStatus.RECEIVED,
        ClientStatus.NEEDS_INPUT,
    )


def can_bring_back(user: User, request: Request) -> bool:
    """Until the Jira sync exists, the PM takes a request out of Later by hand."""
    return _is_owner_pm(user, request) and request.status == ClientStatus.LATER


def can_decline(user: User, request: Request) -> bool:
    """The PM may decline any open request, with a reason, even after the Jira task exists."""
    return _is_owner_pm(user, request) and request.status not in CLOSED_STATUSES


def can_add_note(user: User, request: Request) -> bool:
    """Only the PM writes notes, in an open request, next to the reply box."""
    return _is_owner_pm(user, request) and request.status not in CLOSED_STATUSES


def can_mark_handled(user: User, request: Request) -> bool:
    """ "No reply needed" exists only while the client's reply waits for the PM."""
    return _pm_reason_of(user, request) == PmReason.REPLIED


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
    note: InternalNote | None = None
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


def _edits(request: Request) -> list[Edit]:
    """The request's edits, oldest first. Each revision holds the text before an edit, so
    what it was changed to is the next revision, or the request itself for the latest.

    Private like `_open_question`: it takes a request the caller has already read through
    `requests_for`, so it does not check access again.
    """
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


def _thread(request: Request, request_edits: list[Edit], viewer: User) -> list[ThreadEntry]:
    """Messages, status changes and edits, oldest first, as one conversation; the PM also
    sees their internal notes in it. Private like `_edits`, for the same reason."""
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
    notes = []
    if viewer.role == Role.PM:
        notes = [
            ThreadEntry(at=n.created_at, note=n) for n in request.notes.select_related("author")
        ]
    # A message comes before a status change made at the same moment: the PM's question
    # first, then "Waiting for your input".
    return sorted(
        messages + notes + events + edited + withdrawals,
        key=lambda entry: (entry.at, entry.message is None and entry.note is None),
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
    # When the page was read: opening it marks what came before as seen, not what came after.
    read_at: datetime
    # The request's own files, shown under its text; `all_files` adds those from the thread.
    files: list[Attachment]
    all_files: list[Attachment]
    # Images, PDFs, video and audio, in the same order: the ones the viewer goes through.
    viewable_files: list[Attachment]
    thread: list[ThreadEntry]
    # The first message from others since the viewer last opened the request: the thread
    # draws a "new" line above it. `new_count` says how many there are.
    first_new: Message | None
    new_count: int
    # Why the request waits for the PM; always None for clients.
    pm_reason: PmReason | None
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
    can_move_to_later: bool
    can_bring_back: bool
    can_decline: bool
    can_add_note: bool


def _new_messages(user: User, request: Request, entries: list[ThreadEntry]) -> list[Message]:
    """Messages from others the person has not seen yet, oldest first. Private like
    `_thread`: the request comes from `requests_for`."""
    visit = RequestVisit.objects.filter(request=request, user=user).first()
    return [
        entry.message
        for entry in entries
        if entry.message is not None
        and entry.message.author_id != user.pk
        and (visit is None or entry.message.created_at > visit.seen_at)
    ]


def _pm_reason_of(user: User, request: Request) -> PmReason | None:
    if not _is_owner_pm(user, request):
        return None
    return pm_reasons(requests_for(user).filter(pk=request.pk)).get(request.pk)


def request_page(user: User, pk: int) -> RequestPage:
    """Everything the request card shows. Raises Request.DoesNotExist like `request_for`."""
    read_at = timezone.now()
    request = request_for(user, pk)
    request_edits = _edits(request)
    entries = _thread(request, request_edits, user)
    new = _new_messages(user, request, entries)
    all_files = list(request.attachments.shown().select_related("uploaded_by"))
    # Every request has at least its "Received" event, so this is never empty.
    since = max(entry.at for entry in entries if entry.status)
    return RequestPage(
        viewer=user,
        request=request,
        read_at=read_at,
        files=[file for file in all_files if file.message_id is None],
        all_files=all_files,
        viewable_files=[file for file in all_files if file.shown_in_browser],
        thread=entries,
        first_new=new[0] if new else None,
        new_count=len(new),
        pm_reason=_pm_reason_of(user, request),
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
        can_move_to_later=can_move_to_later(user, request),
        can_bring_back=can_bring_back(user, request),
        can_decline=can_decline(user, request),
        can_add_note=can_add_note(user, request),
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
