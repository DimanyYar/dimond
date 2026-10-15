import hashlib
import logging
import secrets
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


def send_sign_in_link(email: str) -> None:
    """Email a one-time link if this address belongs to an active person.

    Returns the same way whether or not the address has an account, so the sign-in page
    cannot be used to find out who is a client.
    """
    user = User.objects.filter(email=email.strip().lower(), is_active=True).first()
    if user is None:
        return

    now = timezone.now()
    recent = user.sign_in_links.filter(created_at__gte=now - LINK_LIFETIME).count()
    if recent >= MAX_LINKS_PER_LIFETIME:
        logger.warning("Sign-in link limit reached for user %s", user.pk)
        return

    token = secrets.token_urlsafe(32)
    SignInLink.objects.create(user=user, token_hash=_hash(token), expires_at=now + LINK_LIFETIME)
    url = settings.SITE_URL + reverse("sign_in_link", args=[token])
    send_mail(
        subject="Your dimon.d sign-in link",
        message=render_to_string("accounts/email/sign_in_link.txt", {"user": user, "url": url}),
        from_email=None,
        recipient_list=[user.email],
    )


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
    if link is None or link.used_at is not None or link.expires_at <= now:
        logger.warning("Sign-in link rejected: unknown, used or expired")
        raise SignInLinkRejectedError
    if not link.user.is_active:
        logger.warning("Sign-in link rejected: access revoked for user %s", link.user.pk)
        raise SignInLinkRejectedError

    link.used_at = now
    link.save(update_fields=["used_at"])
    return link.user
