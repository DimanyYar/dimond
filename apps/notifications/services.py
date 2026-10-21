import logging
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta

from anymail.exceptions import AnymailError
from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.db import transaction
from django.db.models import Min
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Notify, Role, User
from apps.requests.models import ClientStatus, Request, StatusEvent

from .models import Email, EmailKind, EmailStatus
from .selectors import update_recipients

logger = logging.getLogger(__name__)

# Changes to one request this close together go out as one email (PRD: email notifications):
# a bulk edit in Jira, then the webhook run and the poll, make one email, not three. The window
# opens with the first change and does not restart with later ones, so a request that keeps
# changing still reaches the client within GROUPING of the first news.
GROUPING = timedelta(minutes=3)


def send_email(
    user: User, kind: EmailKind, subject: str, body: str, html: str | None = None
) -> bool:
    """Send one email, plain text and optionally HTML, and write it in the journal, whether it
    went out or not.

    Returns False when the mail server did not take it; the caller decides whether to retry.
    """
    message = EmailMultiAlternatives(subject=subject, body=body, to=[user.email])
    if html is not None:
        message.attach_alternative(html, "text/html")
    journal = Email(user=user, to=user.email, kind=kind, subject=subject)
    try:
        message.send()
    # Mailpit or an SMTP server answers with SMTP errors or a dropped connection; Resend,
    # through anymail, with AnymailError (including its own network errors).
    except (smtplib.SMTPException, OSError, AnymailError) as error:
        logger.warning("Email to user %s failed: %s", user.pk, error)
        journal.status = EmailStatus.FAILED
        journal.error = str(error) or type(error).__name__
    else:
        journal.status = EmailStatus.SENT
        journal.provider_id = _provider_id(message)
    journal.save()
    return journal.status == EmailStatus.SENT


def _provider_id(message: EmailMessage) -> str:
    # Only anymail backends add a status to the message; Django's SMTP backend does not.
    status = getattr(message, "anymail_status", None)
    if status is None or status.message_id is None:
        return ""
    return str(status.message_id)


@dataclass(frozen=True)
class StatusNews:
    """How one status reads in an email: the headline, a line under it, the end of the subject.
    `{who}` is the person who set it; the sync is "The team"."""

    headline: str
    body: str
    subject: str
    pill: str


STATUS_NEWS = {
    ClientStatus.PLANNED: StatusNews(
        "It’s planned",
        "It’s now a task for the team. We’ll email you when work starts.",
        "planned",
        "#ebe7e1",
    ),
    ClientStatus.IN_PROGRESS: StatusNews(
        "The team started on it",
        "It’s in progress now. We’ll email you when it’s ready to check.",
        "in progress",
        "#d9f4f8",
    ),
    ClientStatus.READY_TO_CHECK: StatusNews(
        "Ready for you to check",
        "The team finished the work. Have a look, and if something is off, say so in the request.",
        "ready to check",
        "#fff1e8",
    ),
    ClientStatus.DONE: StatusNews(
        "It’s done",
        "The work is released. If something is still off, write in the request.",
        "done",
        "#e3f7ea",
    ),
    ClientStatus.LATER: StatusNews(
        "{who} moved it to Later",
        "It stays on the list but isn’t planned for now. If it matters more than it looks, "
        "say so in the request.",
        "moved to Later",
        "#ebe7e1",
    ),
    ClientStatus.DECLINED: StatusNews(
        "{who} declined this request",
        "The reason is in the request. If you see it differently, reply there.",
        "declined",
        "#ebe7e1",
    ),
    ClientStatus.WITHDRAWN: StatusNews(
        "{who} withdrew this request",
        "It’s closed and won’t be worked on.",
        "withdrawn",
        "#ebe7e1",
    ),
    ClientStatus.RECEIVED: StatusNews(
        "{who} brought it back",
        "It’s back in the queue to be planned.",
        "back in the queue",
        "#ebe7e1",
    ),
}


def _worth_an_email(event: StatusEvent) -> bool:
    """Status changes the client hears about by email. A question (Needs your input) comes
    with its message; a client's own answer moving the status back is told by that answer."""
    if not event.from_status or event.to_status not in STATUS_NEWS:
        return False
    if event.to_status == ClientStatus.WITHDRAWN:
        return True
    return event.actor is None or event.actor.role == Role.PM


def _headline(event: StatusEvent) -> str:
    who = (event.actor.name or event.actor.email) if event.actor else "The team"
    return STATUS_NEWS[ClientStatus(event.to_status)].headline.format(who=who)


def send_request_updates(now: datetime | None = None) -> int:
    """Email clients about status changes that have settled for GROUPING. Returns how many
    emails went out."""
    settled = (now or timezone.now()) - GROUPING
    due = (
        StatusEvent.objects.filter(emailed=False)
        .values("request")
        .annotate(first=Min("created_at"))
        .filter(first__lte=settled)
        .values_list("request", flat=True)
    )
    return sum(_send_updates_for(request_id) for request_id in due)


def _send_updates_for(request_id: int) -> int:
    with transaction.atomic():
        # skip_locked: a second worker, if one ever runs, takes other requests instead.
        events = list(
            StatusEvent.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(request_id=request_id, emailed=False)
            .select_related("actor", "request__company__owner")
            .order_by("created_at", "pk")
        )
        if not events:
            return 0
        news = [event for event in events if _worth_an_email(event)]
        request = events[0].request
        attempts = [
            _send_update(person, request, theirs)
            for person in (update_recipients(request) if news else [])
            if (theirs := [event for event in news if event.actor_id != person.pk])
        ]
        if attempts and not any(attempts):
            # The mail server took none of them: try the whole batch again next round.
            return 0
        StatusEvent.objects.filter(pk__in=[event.pk for event in events]).update(emailed=True)
    return sum(attempts)


def _send_update(person: User, request: Request, events: list[StatusEvent]) -> bool:
    """One email about the latest change a person did not make themselves, with the earlier
    ones of the same few minutes under it."""
    latest = events[-1]
    news = STATUS_NEWS[ClientStatus(latest.to_status)]
    subject = f"{request.key} · {request.title} — {news.subject}"
    context = {
        "request": request,
        "company": request.company,
        "status_label": ClientStatus(latest.to_status).label,
        "pill": news.pill,
        "headline": _headline(latest),
        "body": news.body,
        "also": [_headline(event) for event in events[:-1]],
        "url": settings.SITE_URL + request.get_absolute_url(),
        "account_url": settings.SITE_URL + reverse("account"),
        "for_team": person.notify == Notify.COMPANY,
    }
    return send_email(
        person,
        EmailKind.REQUEST_UPDATE,
        subject,
        render_to_string("notifications/email/request_update.txt", context),
        render_to_string("notifications/email/request_update.html", context),
    )
