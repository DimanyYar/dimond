from datetime import datetime
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.requests.selectors import requests_for

from .models import Attachment


def attachments_for(user: User) -> QuerySet[Attachment]:
    """Files this person may open: those of the requests they may see (`requests_for`).

    The team opens only files of requests linked to a Jira task, from links in the task (PRD:
    files and attachment links). Requests get that link with the Jira integration (#15), so
    until then the team opens none.
    """
    if user.role == Role.TEAM:
        return Attachment.objects.none()
    return Attachment.objects.filter(request__in=requests_for(user))


def attachment_for(user: User, attachment_id: UUID) -> Attachment:
    """One file this person may open. Raises Attachment.DoesNotExist for anyone else's,
    exactly as for a missing one."""
    return attachments_for(user).get(id=attachment_id)


def public_attachment(token: str) -> Attachment:
    """The file behind a public link, for anyone holding it, while the link works. Raises
    Attachment.DoesNotExist otherwise, exactly as for a made-up link."""
    attachment = Attachment.objects.select_related("request").get(public_token=token)
    if not attachment.is_public(timezone.now()):
        raise Attachment.DoesNotExist
    return attachment


def can_share_again(user: User, attachment: Attachment, now: datetime) -> bool:
    """Only the client's PM, only for a file that had a public link, only once it has closed."""
    request = attachment.request
    return (
        user.is_active
        and user.role == Role.PM
        and request.company.owner_id == user.pk
        and bool(attachment.public_token)
        and not attachment.is_public(now)
    )
