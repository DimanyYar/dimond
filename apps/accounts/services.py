import hashlib
import logging
import secrets
from collections.abc import Iterable
from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.notifications.models import EmailKind
from apps.notifications.services import send_email

from .models import Company, Device, LinkKind, Notify, Role, SignInLink, User

logger = logging.getLogger(__name__)

LIFETIME = {LinkKind.SIGN_IN: timedelta(minutes=15), LinkKind.INVITE: timedelta(days=7)}
# Enough for "didn't arrive, send again" twice; stops anyone flooding a colleague's inbox.
MAX_SIGN_IN_LINKS_PER_LIFETIME = 3

EMAIL = {
    LinkKind.SIGN_IN: (
        EmailKind.SIGN_IN_LINK,
        "Your dimon.d sign-in link",
        "accounts/email/sign_in_link.txt",
    ),
    LinkKind.INVITE: (EmailKind.INVITE, "You're invited to dimon.d", "accounts/email/invite.txt"),
}


class NotAllowedError(Exception):
    """This action does not fit this person's state, e.g. inviting someone who already signed in."""


class SignInLinkRejectedError(Exception):
    """The link is unknown, already used, expired, or its owner lost access."""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def request_sign_in_link(email: str) -> None:
    """Queue a sign-in link if this address belongs to an active person.

    Nothing is sent here: the worker emails it (send_queued_links). The page then answers
    equally fast and the same way for every address, so neither its words nor its speed
    reveal who is a client.
    """
    # Locking the person makes parallel requests wait their turn, so all of them see the
    # real count and the limit holds even when someone fires many requests at once.
    with transaction.atomic():
        user = (
            User.objects.select_for_update()
            .filter(email=email.strip().lower(), is_active=True)
            .first()
        )
        if user is not None:
            _queue_sign_in_link(user)


def _queue_sign_in_link(locked_user: User) -> bool:
    """Queue a sign-in link unless the person hit the limit. The caller holds the row lock."""
    since = timezone.now() - LIFETIME[LinkKind.SIGN_IN]
    recent = locked_user.sign_in_links.filter(kind=LinkKind.SIGN_IN, created_at__gte=since)
    if recent.count() >= MAX_SIGN_IN_LINKS_PER_LIFETIME:
        logger.warning("Sign-in link limit reached for user %s", locked_user.pk)
        return False
    SignInLink.objects.create(user=locked_user)
    return True


@transaction.atomic
def send_sign_in_link(person: User) -> None:
    """The PM's "Send a sign-in link": the same link and limit as the sign-in page.

    Only for someone who has signed in before: a newcomer needs the invite, which asks their
    name first. Raises NotAllowedError otherwise, or when the limit is reached.
    """
    locked = (
        User.objects.select_for_update()
        .filter(pk=person.pk, is_active=True, last_login__isnull=False)
        .first()
    )
    if locked is None or not _queue_sign_in_link(locked):
        raise NotAllowedError


def update_person(person: User, *, name: str, notify: Notify) -> None:
    """The PM corrects a person's name or what they get emails about (PRD: person page)."""
    person.name = name
    person.notify = notify
    person.full_clean()
    person.save(update_fields=["name", "notify"])


def invite(people: Iterable[User], invited_by: User) -> int:
    """Queue an invite for each person who still has access. Returns how many were queued.

    Inviting again is how the PM resends an invite; older invites keep working until they
    expire, so a person who finds the first email can still use it.
    """
    links = [
        SignInLink(user=person, kind=LinkKind.INVITE, invited_by=invited_by)
        for person in people
        if person.is_active
    ]
    SignInLink.objects.bulk_create(links)
    return len(links)


def _queued() -> QuerySet[SignInLink]:
    """Links waiting to be emailed that are still worth sending.

    A request older than its link's lifetime is skipped: the person has moved on and asks
    again if they still need it. Someone who lost access in the meantime gets nothing.
    """
    now = timezone.now()
    still_fresh = Q()
    for kind, lifetime in LIFETIME.items():
        still_fresh |= Q(kind=kind, created_at__gte=now - lifetime)
    return SignInLink.objects.filter(still_fresh, sent_at__isnull=True, user__is_active=True)


def links_queued() -> bool:
    return _queued().exists()


def send_queued_links(limit: int | None = None) -> int:
    """Email the queued links, oldest first, all of them or only `limit` while emails are
    paused. Returns how many went out."""
    ids = _queued().order_by("created_at", "pk").values_list("pk", flat=True)[:limit]
    return sum(_send_one(link_id) for link_id in ids)


def _send_one(link_id: int) -> bool:
    with transaction.atomic():
        # The same filter again, under the lock: between listing and sending a link may have
        # aged out or its owner lost access. skip_locked lets a second worker, if one ever
        # runs, take other links instead of sending the same one twice.
        link = (
            _queued()
            .select_for_update(skip_locked=True, of=("self",))
            .select_related("user__company", "invited_by")
            .filter(pk=link_id)
            .first()
        )
        if link is None:
            return False

        token = secrets.token_urlsafe(32)
        kind, subject, template = EMAIL[LinkKind(link.kind)]
        url = settings.SITE_URL + reverse("sign_in_link", args=[token])
        context = {"user": link.user, "url": url, "invited_by": link.invited_by}
        # The link is marked sent only after the mail server took it: a failed attempt
        # leaves it queued, and the next round tries again with a fresh token.
        if not send_email(link.user, kind, subject, render_to_string(template, context)):
            return False

        now = timezone.now()
        link.token_hash = hash_token(token)
        link.sent_at = now
        link.expires_at = now + LIFETIME[LinkKind(link.kind)]
        link.save(update_fields=["token_hash", "sent_at", "expires_at"])
    return True


def _spend(token: str, kind: LinkKind) -> User:
    # The person's row is locked too: cancelling an invite waits for a sign-in in progress
    # and then sees the spent link (cancel_invite).
    link = (
        SignInLink.objects.select_for_update(of=("self", "user"))
        .select_related("user")
        .filter(token_hash=hash_token(token), kind=kind)
        .first()
    )
    now = timezone.now()
    if (
        link is None
        or link.used_at is not None
        or link.expires_at is None
        or link.expires_at <= now
    ):
        logger.warning("Link rejected: unknown, used or expired")
        raise SignInLinkRejectedError
    if not link.user.is_active:
        logger.warning("Link rejected: access revoked for user %s", link.user.pk)
        raise SignInLinkRejectedError

    link.used_at = now
    link.save(update_fields=["used_at"])
    return link.user


@transaction.atomic
def use_sign_in_link(token: str) -> User:
    """Spend a sign-in link and return its owner, who may now be signed in."""
    return _spend(token, LinkKind.SIGN_IN)


@transaction.atomic
def accept_invite(token: str, name: str, notify: Notify) -> User:
    """Spend an invite, save what the person told us on the welcome screen, return them."""
    user = _spend(token, LinkKind.INVITE)
    user.name = name
    user.notify = notify
    user.save(update_fields=["name", "notify"])
    return user


@transaction.atomic
def revoke_access(people: Iterable[User]) -> int:
    """Turn access off and sign the person out on every device for good (ADR 0015).
    Requests and replies stay with the company. Returns how many lost access.

    A PM who still owns clients keeps access: their clients would vanish from every PM's
    view (ADR 0014). They get a new owner first.
    """
    ids = [person.pk for person in people]
    owns_clients = Exists(Company.objects.filter(owner=OuterRef("pk")))
    revoking = list(
        User.objects.select_for_update().filter(pk__in=ids, is_active=True).exclude(owns_clients)
    )
    for person in revoking:
        person.is_active = False
        person.save(update_fields=["is_active"])
    _end_sessions(revoking)
    # A link from before must not work again if access comes back.
    SignInLink.objects.filter(user__in=revoking, used_at__isnull=True).delete()
    return len(revoking)


def _end_sessions(people: list[User]) -> None:
    """Sign these people out on every device. The caller holds their row locks."""
    for person in people:
        # Every session carries a fingerprint of the password, and Django checks it on each
        # page. A new unusable password changes it, so no device signed in so far gets back
        # in, even sessions from before devices were recorded (ADR 0015).
        person.set_unusable_password()
        person.save(update_fields=["password"])
    devices = Device.objects.filter(user__in=people)
    # Dead already; removed so the person page does not list them.
    Session.objects.filter(session_key__in=devices.values("session_key")).delete()
    devices.delete()


@transaction.atomic
def sign_out_everywhere(person: User) -> None:
    """The PM's "Sign out all devices": every session ends, access stays. The person signs
    in again with an email link (PRD: person page)."""
    _end_sessions(list(User.objects.select_for_update().filter(pk=person.pk)))


def rename_myself(person: User, name: str) -> None:
    """A person changes the name shown on their requests and replies (PRD: account)."""
    person.name = name
    person.save(update_fields=["name"])


def choose_emails(person: User, notify: Notify) -> None:
    """A person picks which requests they get emails about (PRD: email notifications)."""
    person.notify = notify
    person.save(update_fields=["notify"])


@transaction.atomic
def sign_out_other_devices(person: User, this_session_key: str) -> None:
    """The person's own "Sign out other devices": every session but this one ends.

    The same new-password fingerprint as signing out everywhere (ADR 0015), so sessions from
    before devices were recorded end too. The caller gives this session the new fingerprint.
    """
    locked = User.objects.select_for_update().get(pk=person.pk)
    locked.set_unusable_password()
    locked.save(update_fields=["password"])
    person.password = locked.password
    others = Device.objects.filter(user=person).exclude(session_key=this_session_key)
    Session.objects.filter(session_key__in=others.values("session_key")).delete()
    others.delete()


def record_sign_in(user: User, session_key: str, user_agent: str) -> None:
    """Remember the browser behind a new session, for the person page (ADR 0016)."""
    # Sessions that ended (sign-out, expiry) leave rows behind; a sign-in tidies them up.
    alive = Session.objects.filter(expire_date__gt=timezone.now()).values("session_key")
    user.devices.exclude(session_key__in=alive).delete()
    Device.objects.create(user=user, session_key=session_key, user_agent=user_agent[:300])


def forget_device(session_key: str) -> None:
    """The person signed out on this device."""
    Device.objects.filter(session_key=session_key).delete()


@transaction.atomic
def restore_access(pm: User, person: User) -> None:
    """Re-invite: access comes back with a new 7-day link. Devices signed in before the
    revoke stay signed out (ADR 0015)."""
    current = User.objects.select_for_update().filter(pk=person.pk).first()
    if current is None or current.is_active:
        raise NotAllowedError
    current.is_active = True
    current.save(update_fields=["is_active"])
    invite([current], invited_by=pm)


@transaction.atomic
def invite_people(pm: User, company: Company, emails: Iterable[str], notify: Notify) -> int:
    """Add these people to the company and queue an invite for each (PRD: Clients screen).
    They type their name on the welcome screen. Returns how many were invited."""
    people = [
        User.objects.create_user(email, role=Role.CLIENT, company=company, notify=notify)
        for email in emails
    ]
    return invite(people, invited_by=pm)


def _waiting_for_invite(person: User) -> bool:
    return person.is_active and person.last_login is None


@transaction.atomic
def resend_invite(pm: User, person: User) -> None:
    """A fresh 7-day link for someone who has not signed in yet. Locked like cancel_invite,
    so a cancel at the same moment either waits or wins and this one refuses."""
    current = User.objects.select_for_update().filter(pk=person.pk).first()
    if current is None or not _waiting_for_invite(current):
        raise NotAllowedError
    invite([current], invited_by=pm)


@transaction.atomic
def cancel_invite(person: User) -> None:
    """Call off an invite: the person never signed in, so nothing of theirs exists and they
    are removed altogether (decided 2026-10-18). Their links go with them.

    Locked against a sign-in at the same moment: someone who has already spent a link is
    signing in, so the invite stays.
    """
    person = User.objects.select_for_update().get(pk=person.pk)
    if (
        not _waiting_for_invite(person)
        or person.sign_in_links.filter(used_at__isnull=False).exists()
    ):
        raise NotAllowedError
    person.delete()


def change_jira_project(pm: User, company: Company, jira_project: str) -> Company:
    """New tasks for this client go to another Jira project; existing tasks keep their keys.
    `company` comes from `client_for(pm, …)`, so it is one of this PM's own clients."""
    company.jira_project = jira_project
    company.full_clean()
    company.save(update_fields=["jira_project"])
    return company


@transaction.atomic
def create_client(
    pm: User,
    *,
    name: str,
    email_domain: str,
    color: str,
    jira_project: str,
    emails: Iterable[str],
    notify: Notify,
) -> Company:
    """A new client owned by this PM, in its Jira project, with invites for the first people
    if any (PRD: Clients screen; tracker and settings)."""
    company = Company(
        name=name, owner=pm, email_domain=email_domain, color=color, jira_project=jira_project
    )
    company.full_clean()
    company.save()
    invite_people(pm, company, emails, notify)
    return company
