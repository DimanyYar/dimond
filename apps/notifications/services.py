import logging
import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial

from anymail.exceptions import AnymailAPIError, AnymailError
from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.db import transaction
from django.db.models import Min
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Company, Notify, Role, User
from apps.requests.models import (
    CLOSED_STATUSES,
    ClientStatus,
    Message,
    Request,
    StatusEvent,
    WithdrawalRequest,
)

from . import discord
from .models import (
    DiscordPost,
    DiscordPostKind,
    DiscordPostStatus,
    Email,
    EmailKind,
    EmailStatus,
)
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
        # Broad on purpose: whatever breaks one request's emails must not stop every other
        # request's, and a retry would email again the people this batch already reached.
        try:
            # Savepoints, here and below: a database error leaves the rest of the batch usable.
            with transaction.atomic():
                emails = _update_emails(request, events, messages)
        except Exception as error:  # noqa: BLE001 (see the comment above)
            _not_built(request.company.owner, EmailKind.PM_UPDATE, request, error)
            _mark_emailed(events, messages)
            return 0
        attempts: list[bool] = []
        set_aside = False
        for index, (send, _, person, theirs) in enumerate(emails):
            try:
                with transaction.atomic():
                    attempts.append(send(person, request, theirs))
            except Exception as error:  # noqa: BLE001 (see the comment above)
                for _, kind, missed, _ in emails[index:]:
                    _not_built(missed, kind, request, error)
                set_aside = True
                break
            if stop_on_failure and not attempts[-1]:
                break
        if attempts and not any(attempts) and not set_aside:
            # The mail server took none of them: try the whole batch again next round.
            return 0
        _mark_emailed(events, messages)
    return sum(attempts)


UpdateEmail = tuple[Callable[[User, Request, list[News]], bool], EmailKind, User, list[News]]


def _update_emails(
    request: Request, events: list[StatusEvent], messages: list[Message]
) -> list[UpdateEmail]:
    """Who gets an email about this batch of news, and with which news: each client their
    own, then the PM."""
    found = [_status_news(event) for event in events] + [_message_news(m) for m in messages]
    news = sorted((item for item in found if item is not None), key=lambda item: item.at)
    emails: list[UpdateEmail] = [
        (_send_update, EmailKind.REQUEST_UPDATE, person, theirs)
        for person in (update_recipients(request) if news else [])
        if (theirs := [item for item in news if item.actor_id != person.pk])
    ]
    pm = pm_recipient(request)
    pm_news = _pm_news(request, events, messages) if pm else []
    if pm and pm_news:
        emails.append((_send_pm_update, EmailKind.PM_UPDATE, pm, pm_news))
    return emails


def _mark_emailed(events: list[StatusEvent], messages: list[Message]) -> None:
    StatusEvent.objects.filter(pk__in=[event.pk for event in events]).update(emailed=True)
    Message.objects.filter(pk__in=[message.pk for message in messages]).update(emailed=True)


NOT_BUILT = "dimond could not build this email"


def _not_built(person: User, kind: EmailKind, request: Request, error: Exception) -> None:
    """A failed row in Logs → Emails for someone whose email about the request was set
    aside, so the PM sees who missed it and why."""
    logger.exception("Update email about request %s to user %s not built", request.pk, person.pk)
    Email.objects.create(
        user=person,
        to=person.email,
        kind=kind,
        subject=f"{request.key} · {request.title}",
        status=EmailStatus.FAILED,
        error=NOT_BUILT,
        error_detail=f"{type(error).__name__}: {error}",
    )


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


# A report never moves an email back: "delivered" arriving after "marked spam" changes nothing.
# A spam mark outranks a bounce, so it still switches the person's emails off.
REPORT_RANK = {
    EmailStatus.SENT: 0,
    EmailStatus.DELIVERED: 1,
    EmailStatus.BOUNCED: 2,
    EmailStatus.SPAM: 3,
}


def record_delivery_report(
    provider_id: str, status: EmailStatus, at: datetime, detail: str = ""
) -> None:
    """What Resend reported about an email after it took it (ADR 0021): delivered, bounced or
    marked as spam. A spam mark stops that person's emails: they switch to Nothing and their
    page says why. Reports for emails not in the journal, such as another copy of dimond on
    the same Resend account, are ignored."""
    with transaction.atomic():
        email = (
            Email.objects.select_for_update(of=("self",))
            .select_related("user")
            .filter(provider_id=provider_id)
            .exclude(provider_id="")
            .first()
        )
        if email is None or REPORT_RANK[status] <= REPORT_RANK.get(EmailStatus(email.status), 0):
            return
        email.status = status
        if status == EmailStatus.DELIVERED:
            email.delivered_at = at
        if status == EmailStatus.BOUNCED:
            email.error = bounce_in_plain_words(detail)
            email.error_detail = detail
        email.save(update_fields=["status", "delivered_at", "error", "error_detail"])
        if status == EmailStatus.SPAM and email.user is not None:
            email.user.notify = Notify.NONE
            email.user.emails_off_at = at
            email.user.save(update_fields=["notify", "emails_off_at"])


def bounce_in_plain_words(detail: str) -> str:
    """Why the receiving side sent it back, for the PM; Resend's own words stay in the details."""
    if "suppressed" in detail:
        return "Resend blocks this address after earlier bounces"
    if "inbox was full" in detail:
        return "Mailbox is full"
    if "hard bounce" in detail:
        return "Mailbox doesn’t exist"
    return "The receiving server sent it back"


# Discord for the team (PRD: notifications; ADR 0024).

# A post that keeps failing for reasons that may pass (Discord down, too many posts) is tried
# after these pauses, then every hour, for a day. One that Discord refuses outright, a
# deleted webhook or a channel that is not a forum, is not tried again: it would never go.
RETRY_DELAYS = [timedelta(minutes=m) for m in (1, 2, 5, 15, 30)]
RETRY_LATER = timedelta(hours=1)
GIVE_UP_AFTER = timedelta(days=1)
# Room for the type, the urgency and the Jira link in one Discord message.
WHY_LIMIT = 1500
DISCORD_OFF = "Discord was turned off for this client"
GAVE_UP = "Gave up after a day of tries"
TASK_CHANGED = "The request got another task before the post went out"
TEST_TOPIC = "dimond · test"
NO_TASK = "The request no longer has a Jira task"
# A reply's text in the topic; the rest of the 2000 characters is for who wrote and the files.
REPLY_LIMIT = 1500


def queue_task_topic(request: Request) -> None:
    """A topic for the request's new Jira task in the client's Discord forum, sent by the
    worker. Called in the same transaction that links the task, so the two go together."""
    _queue(request, DiscordPostKind.TASK)


def queue_reply(message: Message) -> None:
    """A client's reply, into the topic of the request's task (PRD: Discord: details). Called
    in the transaction that saves the reply."""
    _queue(message.request, DiscordPostKind.REPLY, message=message)


def queue_withdrawal(withdrawal: WithdrawalRequest) -> None:
    """A line in the task's topic: the client asks to withdraw the request, or, once the PM
    decided, what they decided. The team may already be working on it."""
    kind = (
        DiscordPostKind.WITHDRAW_ASK
        if withdrawal.confirmed is None
        else DiscordPostKind.WITHDRAW_DECISION
    )
    _queue(withdrawal.request, kind, withdrawal=withdrawal)


def _queue(
    request: Request,
    kind: DiscordPostKind,
    message: Message | None = None,
    withdrawal: WithdrawalRequest | None = None,
) -> None:
    # Discord is optional per client, and a request without a task has no topic to go to.
    if not request.company.discord_webhook or not request.jira_key:
        return
    DiscordPost.objects.create(
        company=request.company,
        request=request,
        kind=kind,
        task_key=request.jira_key,
        message=message,
        withdrawal=withdrawal,
    )


def send_test_post(company: Company) -> DiscordPost:
    """Send test on the Clients page: a test topic right away, so the PM sees at once whether
    the webhook works and belongs to a forum. `company` is one of this PM's own clients
    (`client_for`). Not retried: the PM is looking at the answer."""
    post = DiscordPost(company=company, kind=DiscordPostKind.TEST)
    text = f"Test from dimond. Topics for {company.name}’s Jira tasks will appear in this forum."
    send = partial(discord.start_topic, company.discord_webhook, TEST_TOPIC, text)
    _try_post(post, send, timezone.now(), retry=False)
    return post


def send_discord_posts(now: datetime | None = None) -> int:
    """The Discord worker's round: every waiting post whose time has come, oldest first.
    Returns how many went out."""
    now = now or timezone.now()
    due = (
        DiscordPost.objects.filter(status=DiscordPostStatus.WAITING, next_try_at__lte=now)
        .select_related(
            "company",
            "request",
            "message__author",
            "withdrawal__requested_by",
            "withdrawal__decided_by",
            "withdrawal__reason",
        )
        .order_by("created_at", "pk")
    )
    return sum(_send_queued(post, now) for post in due)


def _send_queued(post: DiscordPost, now: datetime) -> bool:
    request = post.request
    # Only posts about a request are queued; a test goes out at once.
    assert request is not None
    # Another post of this round may have opened the topic or the PM linked another task.
    request.refresh_from_db(fields=["jira_key", "jira_url", "discord_topic"])
    webhook = post.company.discord_webhook
    if not webhook:
        return _give_up(post, DISCORD_OFF)
    if post.kind == DiscordPostKind.TASK:
        return _send_task_topic(post, request, webhook, now)
    if not request.jira_key:
        return _give_up(post, NO_TASK)
    # The topic reads in the order things happened: a post waits while an earlier one about
    # the same request is still waiting, without spending its tries.
    if DiscordPost.objects.filter(
        request=request, status=DiscordPostStatus.WAITING, pk__lt=post.pk
    ).exists():
        return False
    # A reply belongs to the request, so it goes to the task the request follows now.
    post.task_key = request.jira_key
    text = _topic_post(post)
    return (
        _try_post(post, partial(_post_in_topic, request, webhook, text), now, retry=True)
        is not None
    )


def _give_up(post: DiscordPost, error: str) -> bool:
    post.status, post.error = DiscordPostStatus.FAILED, error
    post.save(update_fields=["status", "error"])
    return False


def _send_task_topic(post: DiscordPost, request: Request, webhook: str, now: datetime) -> bool:
    # While Discord was down, the task may have been deleted in Jira or replaced: the new
    # task has a post of its own, and an old one would post the wrong key or none.
    if request.jira_key != post.task_key:
        return _give_up(post, TASK_CHANGED)
    if request.discord_topic:
        # An older reply, still waiting when the task was linked, went first and opened the
        # topic with this same first post: a second topic would split the conversation.
        post.status, post.tried_at = DiscordPostStatus.SENT, now
        post.save(update_fields=["status", "tried_at"])
        return False
    return _try_post(post, partial(_open_topic, request, webhook), now, retry=True) is not None


def _open_topic(request: Request, webhook: str) -> str:
    name, text = _task_topic(request)
    request.discord_topic = discord.start_topic(webhook, name, text)
    request.save(update_fields=["discord_topic"])
    return request.discord_topic


def _post_in_topic(request: Request, webhook: str, text: str) -> str:
    """dimond makes every topic itself: a task linked before the client had Discord, or whose
    topic someone deleted, gets one now, opened by the task's own first post."""
    if request.discord_topic:
        try:
            return discord.post_in_topic(webhook, request.discord_topic, text)
        except discord.DiscordError as error:
            if not error.topic_gone:
                raise
    return discord.post_in_topic(webhook, _open_topic(request, webhook), text)


def _try_post(
    post: DiscordPost, send: Callable[[], str], now: datetime, *, retry: bool
) -> str | None:
    """One try of the post, written in the journal; returns the topic's id when it went out.
    With `retry`, a failure that may pass leaves the post waiting for its next try."""
    post.attempts += 1
    post.tried_at = now
    try:
        topic = send()
    except discord.DiscordError as error:
        logger.warning("Discord post %s failed: %s", post.pk, error.detail)
        post.error, post.error_detail = error.reason, error.detail
        if not retry or error.retry_after is None:
            post.status = DiscordPostStatus.FAILED
        elif now - post.created_at >= GIVE_UP_AFTER:
            post.status, post.error = DiscordPostStatus.FAILED, GAVE_UP
        else:
            post.next_try_at = now + max(
                _retry_delay(post.attempts), timedelta(seconds=error.retry_after)
            )
        post.save()
        return None
    post.status, post.error, post.error_detail = DiscordPostStatus.SENT, "", ""
    post.save()
    return topic


def _retry_delay(attempts: int) -> timedelta:
    return RETRY_DELAYS[attempts - 1] if attempts <= len(RETRY_DELAYS) else RETRY_LATER


def _task_topic(request: Request) -> tuple[str, str]:
    """The topic's name and first post: what the client asks for and why, and the way to the
    task. The link goes to Jira: the team has no access to dimond's pages."""
    lines = [f"**{request.get_type_display()}** · {request.get_when_needed_display()}"]
    if request.why:
        lines += ["**Why it matters**", discord.fit(request.why, WHY_LIMIT)]
    lines.append(f"Jira: {request.jira_url}")
    return f"{request.jira_key} · {request.title}", "\n".join(lines)


def _topic_post(post: DiscordPost) -> str:
    """What a reply or a withdrawal reads like in the topic. Never a link: the team cannot
    open dimond's pages (PRD: Discord: details)."""
    if post.kind == DiscordPostKind.REPLY:
        assert post.message is not None
        return _client_words(f"**{_who(post.message.author)} replied**", post.message)
    withdrawal = post.withdrawal
    assert withdrawal is not None
    if post.kind == DiscordPostKind.WITHDRAW_ASK:
        return _client_words(
            f"**{_who(withdrawal.requested_by)} asks to withdraw the request.** "
            "The PM will decide whether work on it stops.",
            withdrawal.reason,
        )
    if withdrawal.confirmed:
        return (
            f"**{_who(withdrawal.decided_by)} withdrew the request.** Work on it stops; "
            "the Jira task stays as it is."
        )
    return f"**{_who(withdrawal.decided_by)} kept the request going.** Work on it goes on."


def _client_words(headline: str, message: Message) -> str:
    lines = [headline]
    if message.text:
        lines.append(discord.fit(message.text, REPLY_LIMIT))
    files = message.attachments.count()
    if files:
        lines.append(f"+ {files} file{'s' if files != 1 else ''} in dimond")
    return "\n".join(lines)
