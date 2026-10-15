from django.utils import timezone

from .models import LinkKind, SignInLink
from .services import hash_token


def open_invite(token: str) -> SignInLink | None:
    """The invite behind this token, if it can still be accepted. Reading never spends it."""
    return (
        SignInLink.objects.select_related("user__company", "invited_by")
        .filter(
            token_hash=hash_token(token),
            kind=LinkKind.INVITE,
            used_at__isnull=True,
            expires_at__gt=timezone.now(),
            user__is_active=True,
        )
        .first()
    )
