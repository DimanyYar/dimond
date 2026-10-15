from django.db import transaction

from apps.accounts.models import Role, User

from .models import ClientStatus, Message, Request, RequestType, StatusEvent, WhenNeeded
from .selectors import can_ask_for_input, can_create_requests, can_post_message


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
    return _set_status(request, to_status, actor)


def _set_status(request: Request, to_status: ClientStatus, actor: User | None) -> Request:
    """Change and log the status, whoever is allowed to: callers check that themselves."""
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


@transaction.atomic
def post_message(
    author: User, request: Request, text: str, *, ask_for_input: bool = False
) -> Message:
    """Add a reply to the request's thread.

    With `ask_for_input` the PM's message becomes a question: the request moves to
    "Needs your input". Any client's reply to it hands the request back to the PM as Received
    (PRD: the client answers "Needs info" and the request returns to the PM by itself).
    """
    # Locked and re-read so a reply and a status change at the same moment see one status.
    request = Request.objects.select_for_update().get(pk=request.pk)
    if not can_post_message(author, request):
        raise NotAllowedError
    if ask_for_input and not can_ask_for_input(author, request):
        raise NotAllowedError
    message = Message.objects.create(request=request, author=author, text=text)
    if ask_for_input:
        _set_status(request, ClientStatus.NEEDS_INPUT, author)
    elif author.role == Role.CLIENT and request.status == ClientStatus.NEEDS_INPUT:
        _set_status(request, ClientStatus.RECEIVED, author)
    return message
