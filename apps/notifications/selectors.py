from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db.models import Count, Max, Min, Q
from django.utils import timezone

from apps.accounts.models import Notify, Role, User
from apps.requests.models import Request

from .models import Email, EmailStatus


def update_recipients(request: Request) -> list[User]:
    """The client's people who get emails about this request (PRD: email notifications).

    Read by the worker, not on anyone's behalf: people with access who have signed in at least
    once and did not pick Nothing; "My requests" only for the request's author. Someone invited
    but not signed in yet gets nothing.
    """
    people = User.objects.filter(
        company_id=request.company_id,
        role=Role.CLIENT,
        is_active=True,
        last_login__isnull=False,
    ).exclude(notify=Notify.NONE)
    return [
        person
        for person in people.order_by("pk")
        if person.notify == Notify.COMPANY or person.pk == request.author_id
    ]


def pm_recipient(request: Request) -> User | None:
    """The PM who owns the request's client (ADR 0014), unless they turned these emails off
    on their account page."""
    pm = request.company.owner
    if not pm.is_active or pm.notify == Notify.NONE:
        return None
    return pm


# The Emails tab shows the latest emails; older ones stay in the admin.
EMAILS_SHOWN = 200


def emails_for(pm: User) -> list[Email]:
    """Logs · Emails: every email sent, or tried, to this PM's clients' people and to the PM
    (ADR 0014). An email to someone since deleted (a cancelled invite) has no owner to show
    it to; the admin still lists it."""
    return list(
        Email.objects.filter(Q(user=pm) | Q(user__company__owner=pm))
        .select_related("user__company")
        .order_by("-tried_at", "-pk")[:EMAILS_SHOWN]
    )


SERVER_UNREACHABLE = "Mail server unreachable"
DAILY_LIMIT_REACHED = "Daily limit reached"
# Failures that stop every email, not just one: a refused address does not pause the rest.
PAUSING = (SERVER_UNREACHABLE, DAILY_LIMIT_REACHED)
# Emails pause after a minute of nothing but such failures, and while paused one email is
# tried every 5 minutes (PRD: email notifications). Sign-in links live 15 minutes, so a short
# outage still lets people in.
PAUSE_AFTER = timedelta(minutes=1)
PROBE_EVERY = timedelta(minutes=5)


@dataclass(frozen=True)
class MailPause:
    since: datetime
    reason: str
    next_try: datetime


def mail_pause(now: datetime | None = None) -> MailPause | None:
    """Whether emails are paused: since the last email that went out, every try failed with
    the server unreachable or the daily limit, for longer than PAUSE_AFTER. A pause with
    nothing tried for two rounds is over: nothing waits any more."""
    now = now or timezone.now()
    last_sent = Email.objects.filter(status=EmailStatus.SENT).aggregate(at=Max("tried_at"))["at"]
    failing = Email.objects.filter(status=EmailStatus.FAILED, error__in=PAUSING)
    if last_sent is not None:
        failing = failing.filter(tried_at__gt=last_sent)
    streak = failing.aggregate(first=Min("created_at"), last=Max("tried_at"), count=Count("pk"))
    if not streak["count"]:
        return None
    since = max(streak["first"], last_sent) if last_sent else streak["first"]
    if since > now - PAUSE_AFTER or streak["last"] < now - 2 * PROBE_EVERY:
        return None
    reason = failing.order_by("-tried_at").values_list("error", flat=True)[0]
    return MailPause(since=since, reason=reason, next_try=streak["last"] + PROBE_EVERY)


@dataclass(frozen=True)
class DailyCount:
    sent: int
    limit: int

    @property
    def over(self) -> bool:
        return self.sent >= self.limit

    @property
    def percent(self) -> int:
        """How full the bar is; the number itself is never capped (3721 of 100 is shown)."""
        return min(100, round(100 * self.sent / self.limit)) if self.limit else 100


def sent_today(now: datetime | None = None) -> DailyCount:
    """Emails that went out since midnight UTC, when Resend's daily count starts again. For
    every PM together: the limit is one for the whole of dimond."""
    now = now or timezone.now()
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    sent = Email.objects.exclude(status=EmailStatus.FAILED).filter(tried_at__gte=midnight).count()
    return DailyCount(sent=sent, limit=settings.EMAIL_DAILY_LIMIT)
