import hashlib
import logging
import secrets
import smtplib
from collections.abc import Iterable
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q, QuerySet
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import LinkKind, Notify, SignInLink, User

logger = logging.getLogger(__name__)

LIFETIME = {LinkKind.SIGN_IN: timedelta(minutes=15), LinkKind.INVITE: timedelta(days=7)}
# Enough for "didn't arrive, send again" twice; stops anyone flooding a colleague's inbox.
MAX_SIGN_IN_LINKS_PER_LIFETIME = 3

EMAIL = {
    LinkKind.SIGN_IN: ("Your dimon.d sign-in link", "accounts/email/sign_in_link.txt"),
    LinkKind.INVITE: ("You're invited to dimon.d", "accounts/email/invite.txt"),
}


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
        if user is None:
            return

        since = timezone.now() - LIFETIME[LinkKind.SIGN_IN]
        recent = user.sign_in_links.filter(kind=LinkKind.SIGN_IN, created_at__gte=since)
        if recent.count() >= MAX_SIGN_IN_LINKS_PER_LIFETIME:
            logger.warning("Sign-in link limit reached for user %s", user.pk)
            return

        SignInLink.objects.create(user=user)


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


def send_queued_links() -> int:
    """Email every queued link. Returns how many went out."""
    return sum(_send_one(link_id) for link_id in _queued().values_list("pk", flat=True))


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
        now = timezone.now()
        link.token_hash = hash_token(token)
        link.sent_at = now
        link.expires_at = now + LIFETIME[LinkKind(link.kind)]
        link.save(update_fields=["token_hash", "sent_at", "expires_at"])

        subject, template = EMAIL[LinkKind(link.kind)]
        url = settings.SITE_URL + reverse("sign_in_link", args=[token])
        context = {"user": link.user, "url": url, "invited_by": link.invited_by}
        try:
            send_mail(
                subject=subject,
                message=render_to_string(template, context),
                from_email=None,
                recipient_list=[link.user.email],
            )
        except (smtplib.SMTPException, OSError):
            # Undo the "sent" mark so the next run tries this link again.
            logger.exception("Email to user %s failed; will retry", link.user.pk)
            transaction.set_rollback(True)
            return False
    return True


def _spend(token: str, kind: LinkKind) -> User:
    link = (
        SignInLink.objects.select_for_update()
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


def revoke_access(people: Iterable[User]) -> int:
    """Turn access off. Sessions end on the next page load; requests and replies stay.

    A PM who still owns clients keeps access: their clients would vanish from every PM's
    view (ADR 0014). They get a new owner first.
    """
    ids = [person.pk for person in people]
    return User.objects.filter(pk__in=ids, is_active=True, clients__isnull=True).update(
        is_active=False
    )
