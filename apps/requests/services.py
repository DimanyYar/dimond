from django.db import transaction

from apps.accounts.models import Role, User

from .models import ClientStatus, Request, RequestType, StatusEvent, WhenNeeded
from .selectors import can_create_requests


class NotAllowedError(Exception):
    """This person may not perform this action."""


@transaction.atomic
def create_request(
    author: User,
    type: RequestType,
    title: str,
    why: str,
    when_needed: WhenNeeded,
    description: str = "",
    steps: str = "",
    link: str = "",
) -> Request:
    """Save a client's request as Received. The company comes from the author's account."""
    if not can_create_requests(author):
        raise NotAllowedError
    # Always true for a client (a database constraint); spelled out for the type checker.
    assert author.company is not None
    request = Request.objects.create(
        company=author.company,
        author=author,
        type=type,
        title=title,
        why=why,
        when_needed=when_needed,
        description=description,
        steps=steps,
        link=link,
    )
    StatusEvent.objects.create(request=request, to_status=ClientStatus.RECEIVED, actor=author)
    return request


@transaction.atomic
def change_status(request: Request, to_status: ClientStatus, actor: User | None) -> Request:
    """Move a request to another status and log it. `actor=None` means the Jira sync did it.

    Setting the status it already has does nothing, so a repeated sync run logs no noise.
    """
    if actor is not None and actor.role != Role.PM:
        raise NotAllowedError
    # Locked so the sync and the PM changing it at the same moment both end up in the log
    # with the right "from" status.
    request = Request.objects.select_for_update().get(pk=request.pk)
    if request.status == to_status:
        return request
    StatusEvent.objects.create(
        request=request, from_status=request.status, to_status=to_status, actor=actor
    )
    request.status = to_status
    request.save(update_fields=["status", "updated_at"])
    return request
