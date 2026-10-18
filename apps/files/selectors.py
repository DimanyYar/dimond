from uuid import UUID

from django.db.models import QuerySet

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
