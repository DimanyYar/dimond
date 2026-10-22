import logging
import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from anymail.exceptions import AnymailAPIError, AnymailError
from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.db import transaction
from django.db.models import Min
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Notify, Role, User
from apps.requests.models import (
    CLOSED_STATUSES,
    ClientStatus,
    Message,
    Request,
    StatusEvent,
    WithdrawalRequest,
)

from .models import Email, EmailKind, EmailStatus
from .selectors import (
    DAILY_LIMIT_REACHED,
    SERVER_UNREACHABLE,
    mail_pause,
    pm_recipient,
    update_recipients,
)

logger = logging.getLogger(__name__)

# Changes to one request this close together go out as one email (PRD: email notifications):
# a bulk edit in Jira, then the webhook run and the poll, make one email, not three. The window
# opens with the first change and does not restart with later ones, so a request that keeps
# changing still reaches the client within GROUPING of the first news.
GROUPING = timedelta(minutes=3)


# A failed email that is tried again within this long keeps its row in the journal.
SAME_EMAIL = timedelta(days=1)


def send_email(
    user: User, kind: EmailKind, subject: str, body: str, html: str | None = None
) -> bool:
    """Send one email, plain text and optionally HTML, and write it in the journal, whether it
    went out or not. Another try of an email that did not go out (same person, kind and
    subject, within a day) updates its row: one row per email, with the number of tries.

    Returns False when the mail server did not take it; the caller decides whether to retry.
    """
    message = EmailMultiAlternatives(subject=subject, body=body, to=[user.email])
    if html is not None:
        message.attach_alternative(html, "text/html")
    now = timezone.now()
    journal = Email.objects.filter(
        user=user,
        kind=kind,
        subject=subject,
        status=EmailStatus.FAILED,
        created_at__gte=now - SAME_EMAIL,
    ).order_by("-tried_at").first() or Email(
        user=user, to=user.email, kind=kind, subject=subject, attempts=0
    )
    journal.attempts += 1
    journal.tried_at = now
    try:
        message.send()
    # Mailpit or an SMTP server answers with SMTP errors or a dropped connection; Resend,
    # through anymail, with AnymailError (including its own network errors).
    except (smtplib.SMTPException, OSError, AnymailError) as error:
        logger.warning("Email to user %s failed: %s", user.pk, error)
        journal.status = EmailStatus.FAILED
        journal.error = in_plain_words(error)
        journal.error_detail = str(error) or type(error).__name__
    else:
        journal.status = EmailStatus.SENT
        journal.provider_id = _provider_id(message)
        journal.error = ""
        journal.error_detail = ""
    journal.save()
    return journal.status == EmailStatus.SENT


def in_plain_words(error: Exception) -> str:
    """What went wrong, for the PM's eyes: the mail server's own words stay in the details."""
    # Resend answers 429 for a few things; only the daily quota waits for tomorrow. The
    # monthly quota and a burst over the per-second rate read as an ordinary refusal.
    if isinstance(error, AnymailAPIError) and _resend_error_name(error) == "daily_quota_exceeded":
        return DAILY_LIMIT_REACHED
    if isinstance(error, AnymailAPIError) and error.status_code is not None:
        return "Resend refused it"
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "The mail server refused the address"
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "The mail server refused the login"
    if isinstance(error, smtplib.SMTPException):
        return "The mail server refused it"
    # No such host, connection refused, timed out, or Resend not answering at all.
    return SERVER_UNREACHABLE


def _resend_error_name(error: AnymailAPIError) -> str:
    try:
        answer = error.response.json()
    # Not every refusal comes with a JSON body, e.g. an error page from a proxy.
    except (AttributeError, ValueError):
        return ""
    return str(answer.get("name", "")) if isinstance(answer, dict) else ""


def _provider_id(message: EmailMessage) -> str:
    # Only anymail backends add a status to the message; Django's SMTP backend does not.
    status = getattr(message, "anymail_status", None)
    if status is None or status.message_id is None:
        return ""
    return str(status.message_id)


def send_queued_emails(now: datetime | None = None) -> int:
    """The worker's round: sign-in links first, someone is waiting at the sign-in page for
    them, then request updates. While emails are paused (PRD: email notifications), only one
    email is tried, once every PROBE_EVERY. Returns how many went out."""
    # Imported here: accounts sends its emails through this module, so a top-level import
    # would go round in a circle.
    from apps.accounts.services import send_queued_links

    now = now or timezone.now()
    pause = mail_pause(now)
    if pause is None:
        return send_queued_links() + send_request_updates(now)
    if now < pause.next_try:
        return 0
    return _try_one_email(now)


def try_emails_now() -> bool:
    """The PM's "Try now" on a pause: one email at once instead of at the next try. Returns
    whether emails are going out again."""
    now = timezone.now()
    _try_one_email(now)
    return mail_pause(now) is None


def _try_one_email(now: datetime) -> int:
    """While paused, one try tells whether the mail server is back: a queued sign-in link if
    there is one, else the first request's updates, stopping at the first that fails."""
    from apps.accounts.services import links_queued, send_queued_links

    if links_queued():
        return send_queued_links(limit=1)
    return send_request_updates(now, limit=1, stop_on_failure=True)


# The colour of the status label at the top of an email, as on the board.
PILLS = {
    ClientStatus.NEEDS_INPUT: "#ff5600",
    ClientStatus.IN_PROGRESS: "#d9f4f8",
    ClientStatus.READY_TO_CHECK: "#fff1e8",
    ClientStatus.DONE: "#e3f7ea",
}
PLAIN_PILL = "#ebe7e1"


@dataclass(frozen=True)
class StatusNews:
    """How one status reads in an email: the headline, a line under it, the end of the subject.
    `{who}` is the person who set it; the sync is "The team"."""

    headline: str
    body: str
    subject: str


STATUS_NEWS = {
    ClientStatus.PLANNED: StatusNews(
        "It’s planned",
        "It’s now a task for the team. We’ll email you when work starts.",
        "planned",
    ),
    ClientStatus.IN_PROGRESS: StatusNews(
        "The team started on it",
        "It’s in progress now. We’ll email you when it’s ready to check.",
        "in progress",
    ),
    ClientStatus.READY_TO_CHECK: StatusNews(
        "Ready for you to check",
        "The team finished the work. Have a look, and if something is off, say so in the request.",
        "ready to check",
    ),
    ClientStatus.DONE: StatusNews(
        "It’s done",
        "The work is released. If something is still off, write in the request.",
        "done",
    ),
    ClientStatus.LATER: StatusNews(
        "{who} moved it to Later",
        "It stays on the list but isn’t planned for now. If it matters more than it looks, "
        "say so in the request.",
        "moved to Later",
    ),
    ClientStatus.DECLINED: StatusNews(
        "{who} declined this request",
        "The reason is in the request. If you see it differently, reply there.",
        "declined",
    ),
    ClientStatus.WITHDRAWN: StatusNews(
        "{who} withdrew this request",
        "It’s closed and won’t be worked on.",
        "withdrawn",
    ),
    ClientStatus.RECEIVED: StatusNews(
        "{who} brought it back",
        "It’s back in the queue to be planned.",
        "back in the queue",
    ),
}

# What a message says in an email: never its text, which stays in dimond (PRD: email
# notifications), only who wrote and what kind of message it is.
TEAM_QUESTION = StatusNews(
    "The team has a question",
    "Someone on the team needs a detail from you before they go on. Open the request to read "
    "the question. Your answer goes straight to them.",
    "the team has a question",
)
PM_QUESTION = StatusNews(
    "{who} has a question",
    "Open the request to read it and answer.",
    "a question from {who}",
)
PM_REPLY = StatusNews("{who} replied", "Open the request to read the reply.", "{who} replied")
CLIENT_MESSAGE = StatusNews(
    "{who} wrote in the request", "Open the request to read it.", "new message from {who}"
)


# What the PM hears about (PRD: email notifications): the same things Needs you shows.
NEW_REQUEST = StatusNews(
    "New request from {who}", "It waits in Needs you until you triage it.", "new request"
)
CLIENT_REPLY = StatusNews(
    "{who} replied", "The request is back in Needs you with “Client replied”.", "{who} replied"
)
WITHDRAW_ASK = StatusNews(
    "{who} asks to withdraw",
    "The team may have started on it. Decide in the request: Withdraw it or Keep it going.",
    "asks to withdraw",
)


@dataclass(frozen=True)
class News:
    """One thing that happened to a request, as a line of an email."""

    kind: StatusNews
    at: datetime
    actor_id: int | None
    headline: str
    body: str
    subject: str


def _who(person: User | None) -> str:
    return (person.name or person.email) if person else "The team"


def _news(kind: StatusNews, at: datetime, actor: User | None) -> News:
    who = _who(actor)
    return News(
        kind=kind,
        at=at,
        actor_id=actor.pk if actor else None,
        headline=kind.headline.format(who=who),
        body=kind.body,
        subject=kind.subject.format(who=who),
    )


def _status_news(event: StatusEvent) -> News | None:
    """Status changes the client hears about by email. A question (Needs your input) is told
    by its message; a client's own answer moving the status back is told by that answer."""
    if not event.from_status or event.to_status not in STATUS_NEWS:
        return None
    if event.to_status != ClientStatus.WITHDRAWN and (
        event.actor is not None and event.actor.role != Role.PM
    ):
        return None
    return _news(STATUS_NEWS[ClientStatus(event.to_status)], event.created_at, event.actor)


def _message_news(message: Message) -> News:
    if message.from_team:
        kind = TEAM_QUESTION
    elif message.author is not None and message.author.role == Role.PM:
        kind = PM_QUESTION if message.asks_for_input else PM_REPLY
    else:
        kind = CLIENT_MESSAGE
    return _news(kind, message.created_at, message.author)


def _pm_news(request: Request, events: list[StatusEvent], messages: list[Message]) -> list[News]:
    """What the PM hears about: a new request, a client's reply or ask to withdraw. A closed
    request has left Needs you, so nothing about it is news, even a request created and
    withdrawn within the same few minutes."""
    if request.status in CLOSED_STATUSES:
        return []
    news = [
        _news(NEW_REQUEST, event.created_at, event.actor)
        for event in events
        if not event.from_status
    ]
    asks = set(
        WithdrawalRequest.objects.filter(
            reason__in=[message.pk for message in messages]
        ).values_list("reason_id", flat=True)
    )
    news += [
        _news(
            WITHDRAW_ASK if message.pk in asks else CLIENT_REPLY,
            message.created_at,
            message.author,
        )
        for message in messages
        if message.author is not None and message.author.role == Role.CLIENT
    ]
    return sorted(news, key=lambda item: item.at)


def send_request_updates(
    now: datetime | None = None, limit: int | None = None, stop_on_failure: bool = False
) -> int:
    """Email clients and their PM about what happened to requests once it has settled for
    GROUPING: status changes and new messages. Returns how many emails went out."""
    settled = (now or timezone.now()) - GROUPING
    first_news: dict[int, datetime] = {}
    for model in (StatusEvent, Message):
        pending = (
            model.objects.filter(emailed=False)
            .values("request")
            .annotate(first=Min("created_at"))
            .values_list("request", "first")
        )
        for request_id, first in pending:
            first_news[request_id] = min(first, first_news.get(request_id, first))
    due = [request_id for request_id, first in first_news.items() if first <= settled]
    return sum(_send_updates_for(request_id, stop_on_failure) for request_id in sorted(due)[:limit])


def _send_updates_for(request_id: int, stop_on_failure: bool) -> int:
    with transaction.atomic():
        # skip_locked: a second worker, if one ever runs, takes other requests instead.
        events = list(
            StatusEvent.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(request_id=request_id, emailed=False)
            .select_related("actor")
            .order_by("created_at", "pk")
        )
        messages = list(
            Message.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(request_id=request_id, emailed=False)
            .select_related("author")
            .order_by("created_at", "pk")
        )
        if not events and not messages:
            return 0
        request = Request.objects.select_related("company__owner").get(pk=request_id)
        found = [_status_news(event) for event in events] + [_message_news(m) for m in messages]
        news = sorted((item for item in found if item is not None), key=lambda item: item.at)
        emails: list[tuple[Callable[[User, Request, list[News]], bool], User, list[News]]] = [
            (_send_update, person, theirs)
            for person in (update_recipients(request) if news else [])
            if (theirs := [item for item in news if item.actor_id != person.pk])
        ]
        pm = pm_recipient(request)
        pm_news = _pm_news(request, events, messages) if pm else []
        if pm and pm_news:
            emails.append((_send_pm_update, pm, pm_news))
        attempts: list[bool] = []
        for send, person, theirs in emails:
            attempts.append(send(person, request, theirs))
            if stop_on_failure and not attempts[-1]:
                break
        if attempts and not any(attempts):
            # The mail server took none of them: try the whole batch again next round.
            return 0
        StatusEvent.objects.filter(pk__in=[event.pk for event in events]).update(emailed=True)
        Message.objects.filter(pk__in=[message.pk for message in messages]).update(emailed=True)
    return sum(attempts)


def _context(request: Request, news: list[News]) -> dict[str, object]:
    latest = news[-1]
    return {
        "request": request,
        "company": request.company,
        "headline": latest.headline,
        "body": latest.body,
        "also": [item.headline for item in news[:-1]],
        "url": settings.SITE_URL + request.get_absolute_url(),
        "account_url": settings.SITE_URL + reverse("account"),
    }


def _send_update(person: User, request: Request, news: list[News]) -> bool:
    """One email about the latest news a person did not make themselves, with the earlier
    news of the same few minutes under it."""
    status = ClientStatus(request.status)
    context = _context(request, news) | {
        "status_label": status.label,
        "pill": PILLS.get(status, PLAIN_PILL),
        "for_team": person.notify == Notify.COMPANY,
    }
    return send_email(
        person,
        EmailKind.REQUEST_UPDATE,
        f"{request.key} · {request.title} — {news[-1].subject}",
        render_to_string("notifications/email/request_update.txt", context),
        render_to_string("notifications/email/request_update.html", context),
    )


def _send_pm_update(pm: User, request: Request, news: list[News]) -> bool:
    """The PM's email about their client's request (mockup 5.7 · Email to the PM)."""
    status = ClientStatus(request.status)
    if news[-1].kind is NEW_REQUEST:
        meta = f"{request.get_type_display()} · asked for: {request.get_when_needed_display()}"
    else:
        meta = status.label + (f" · {request.jira_key}" if request.jira_key else "")
    context = _context(request, news) | {"meta": meta}
    return send_email(
        pm,
        EmailKind.PM_UPDATE,
        f"{request.company.name} · {request.key} · {request.title} — {news[-1].subject}",
        render_to_string("notifications/email/pm_update.txt", context),
        render_to_string("notifications/email/pm_update.html", context),
    )
