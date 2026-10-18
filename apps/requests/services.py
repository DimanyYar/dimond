from collections.abc import Collection, Sequence
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.files.services import attach_files, removable_files, remove_files
from apps.files.uploads import CheckedFile

from .models import (
    REQUEST_FIELDS,
    ClientStatus,
    Message,
    Request,
    RequestRevision,
    RequestType,
    StatusEvent,
    WhenNeeded,
    WithdrawalRequest,
)
from .selectors import (
    can_ask_for_input,
    can_ask_to_withdraw,
    can_create_requests,
    can_decide_withdrawal,
    can_edit_request,
    can_post_message,
    can_withdraw_now,
)


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
    files: Sequence[CheckedFile] = (),
    from_voice: bool = False,
) -> Request:
    """Save a client's request as Received, with its files. The company comes from the
    author's account."""
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
        from_voice=from_voice,
    )
    StatusEvent.objects.create(request=request, to_status=ClientStatus.RECEIVED, actor=author)
    attach_files(request, author, files)
    return request


def create_voice_request(
    author: User,
    title: str,
    recording: CheckedFile,
    type: RequestType = RequestType.NOT_SURE,
    text: str = "",
) -> Request:
    """Save a request the client said out loud ("Just say it"). Why it matters and when it is
    needed are in the recording, so they are not asked; the PM sorts the type and urgency. The
    browser's text draft, if any, becomes the description."""
    return create_request(
        author,
        type,
        title,
        "",
        WhenNeeded.NOT_SURE,
        description=text,
        files=[recording],
        from_voice=True,
    )


@transaction.atomic
def edit_request(
    editor: User,
    request: Request,
    type: RequestType,
    title: str,
    why: str,
    when_needed: WhenNeeded,
    description: str = "",
    steps: str = "",
    link: str = "",
    add_files: Sequence[CheckedFile] = (),
    remove_file_ids: Collection[UUID] = (),
) -> Request:
    """Rewrite the request, add or remove its files, and keep the old text as a revision for
    its "edited" history; the files added and removed point at that revision.

    Saving the form unchanged keeps no revision, so the request is not marked edited for
    nothing. The status stays: an edit is not an answer to the PM's question.
    """
    # Locked and re-read so an edit and the PM planning it at the same moment see one status.
    request = Request.objects.select_for_update().get(pk=request.pk)
    if not can_edit_request(editor, request):
        raise NotAllowedError
    new = {
        "type": type,
        "title": title,
        "why": why,
        "when_needed": when_needed,
        "description": description,
        "steps": steps,
        "link": link,
    }
    to_remove = removable_files(request, remove_file_ids)
    text_unchanged = all(getattr(request, field) == new[field] for field in REQUEST_FIELDS)
    if text_unchanged and not add_files and not to_remove:
        return request
    revision = RequestRevision.objects.create(
        request=request,
        editor=editor,
        **{field: getattr(request, field) for field in REQUEST_FIELDS},
    )
    attach_files(request, editor, add_files, added_in=revision)
    remove_files(to_remove, revision)
    for field in REQUEST_FIELDS:
        setattr(request, field, new[field])
    request.save()
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
    author: User,
    request: Request,
    text: str,
    *,
    ask_for_input: bool = False,
    files: Sequence[CheckedFile] = (),
) -> Message:
    """Add a reply, with files if any, to the request's thread. Files go in this way for as
    long as the thread is open, whatever the status (PRD: editing a request and attachments).

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
    message = Message.objects.create(
        request=request, author=author, text=text, asks_for_input=ask_for_input
    )
    attach_files(request, author, files, message=message)
    if ask_for_input:
        _set_status(request, ClientStatus.NEEDS_INPUT, author)
    elif author.role == Role.CLIENT and request.status == ClientStatus.NEEDS_INPUT:
        _set_status(request, ClientStatus.RECEIVED, author)
    return message


@transaction.atomic
def withdraw_request(client: User, request: Request, reason: str = "") -> WithdrawalRequest | None:
    """The client's "Withdraw": closes the request at once while nobody works on it yet,
    otherwise asks the PM, who may already have the team on it (PRD: editing a request).

    The reason goes into the thread as the client's message. It is optional for an instant
    withdrawal and required for an ask: the PM needs it to decide. Returns the ask, or None
    when the request was withdrawn on the spot.
    """
    # Locked and re-read so a withdrawal and the PM planning it at the same moment see one
    # status and pick the right path.
    request = Request.objects.select_for_update().get(pk=request.pk)
    if can_withdraw_now(client, request):
        if reason:
            Message.objects.create(request=request, author=client, text=reason)
        _set_status(request, ClientStatus.WITHDRAWN, client)
        return None
    if not can_ask_to_withdraw(client, request):
        raise NotAllowedError
    if not reason:
        raise ValueError("A reason is required to ask the PM to withdraw a request.")
    message = Message.objects.create(request=request, author=client, text=reason)
    return WithdrawalRequest.objects.create(request=request, requested_by=client, reason=message)


@transaction.atomic
def decide_withdrawal(pm: User, request: Request, *, confirm: bool) -> Request:
    """The PM's answer to a client's ask: withdraw the request, or keep it going.

    dimond leaves the Jira task alone (ADR 0010); closing it there is the PM's own step.
    """
    request = Request.objects.select_for_update().get(pk=request.pk)
    if not can_decide_withdrawal(pm, request):
        raise NotAllowedError
    WithdrawalRequest.objects.filter(request=request, confirmed__isnull=True).update(
        confirmed=confirm, decided_by=pm, decided_at=timezone.now()
    )
    if confirm:
        return _set_status(request, ClientStatus.WITHDRAWN, pm)
    return request
