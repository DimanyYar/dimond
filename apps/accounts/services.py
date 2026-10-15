import hashlib
import logging
import secrets
import smtplib
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import SignInLink, User

logger = logging.getLogger(__name__)

LINK_LIFETIME = timedelta(minutes=15)
# Enough for "didn't arrive, send again" twice; stops anyone flooding a colleague's inbox.
MAX_LINKS_PER_LIFETIME = 3


class SignInLinkRejectedError(Exception):
    """The link is unknown, already used, expired, or its owner lost access."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def request_sign_in_link(email: str) -> None:
    """Queue a sign-in link if this address belongs to an active person.

    Nothing is sent here: the worker emails it (send_queued_sign_in_links). The page then
    answers equally fast and the same way for every address, so neither its words nor its
    speed reveal who is a client.
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

        since = timezone.now() - LINK_LIFETIME
        if user.sign_in_links.filter(created_at__gte=since).count() >= MAX_LINKS_PER_LIFETIME:
            logger.warning("Sign-in link limit reached for user %s", user.pk)
            return

        SignInLink.objects.create(user=user)


def send_queued_sign_in_links() -> int:
    """Email every queued link that is still worth sending. Returns how many went out.

    A request older than the link lifetime is skipped: the person has moved on and asks
    again if they still need it.
    """
    sent = 0
    since = timezone.now() - LINK_LIFETIME
    queued = SignInLink.objects.filter(
        sent_at__isnull=True, created_at__gte=since, user__is_active=True
    )
    for link_id in queued.values_list("pk", flat=True):
        if _send_one(link_id):
            sent += 1
    return sent


def _send_one(link_id: int) -> bool:
    with transaction.atomic():
        # skip_locked lets a second worker, if one ever runs, take other links instead of
        # sending the same one twice.
        link = (
            SignInLink.objects.select_for_update(skip_locked=True)
            .select_related("user")
            .filter(pk=link_id, sent_at__isnull=True)
            .first()
        )
        if link is None:
            return False

        token = secrets.token_urlsafe(32)
        now = timezone.now()
        link.token_hash = _hash(token)
        link.sent_at = now
        link.expires_at = now + LINK_LIFETIME
        link.save(update_fields=["token_hash", "sent_at", "expires_at"])

        url = settings.SITE_URL + reverse("sign_in_link", args=[token])
        try:
            send_mail(
                subject="Your dimon.d sign-in link",
                message=render_to_string(
                    "accounts/email/sign_in_link.txt", {"user": link.user, "url": url}
                ),
                from_email=None,
                recipient_list=[link.user.email],
            )
        except (smtplib.SMTPException, OSError):
            # Undo the "sent" mark so the next run tries this link again.
            logger.exception("Sign-in email to user %s failed; will retry", link.user.pk)
            transaction.set_rollback(True)
            return False
    return True


@transaction.atomic
def use_sign_in_link(token: str) -> User:
    """Spend the link and return its owner, who may now be signed in."""
    link = (
        SignInLink.objects.select_for_update()
        .select_related("user")
        .filter(token_hash=_hash(token))
        .first()
    )
    now = timezone.now()
    if (
        link is None
        or link.used_at is not None
        or link.expires_at is None
        or link.expires_at <= now
    ):
        logger.warning("Sign-in link rejected: unknown, used or expired")
        raise SignInLinkRejectedError
    if not link.user.is_active:
        logger.warning("Sign-in link rejected: access revoked for user %s", link.user.pk)
        raise SignInLinkRejectedError

    link.used_at = now
    link.save(update_fields=["used_at"])
    return link.user
