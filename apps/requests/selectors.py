from collections.abc import Iterable
from datetime import datetime, timedelta

from django.db import models
from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from apps.accounts.models import Role, User

from .models import (
    BOARD_COLUMNS,
    CLOSED_STATUSES,
    IN_WORK_STATUSES,
    BoardColumn,
    ClientStatus,
    Request,
    RequestType,
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
    user: User, *, show: Show, type: RequestType | None, since: datetime | None
) -> QuerySet[Request]:
    """The requests on "My requests", newest first. Later counts as open: it may come back."""
    requests = _created_since(user, since)
    if show == Show.OPEN:
        requests = requests.exclude(status__in=CLOSED_STATUSES)
    elif show == Show.CLOSED:
        requests = requests.filter(status__in=CLOSED_STATUSES)
    if type is not None:
        requests = requests.filter(type=type)
    return requests


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
