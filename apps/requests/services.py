from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.files.models import Attachment
from apps.files.services import attach_files, public_link, removable_files, remove_files
from apps.files.uploads import CheckedFile
from apps.tracker.base import Issue, Tracker, TrackerError
from apps.tracker.connection import current_tracker

from .models import (
    REQUEST_FIELDS,
    ClientStatus,
    InternalNote,
    Message,
    Request,
    RequestRevision,
    RequestType,
    RequestVisit,
    StatusEvent,
    WhenNeeded,
    WithdrawalRequest,
)
from .selectors import (
    can_add_note,
    can_ask_for_input,
    can_ask_to_withdraw,
    can_bring_back,
    can_create_jira_task,
    can_create_requests,
    can_decide_withdrawal,
    can_decline,
    can_edit_request,
    can_mark_handled,
    can_move_to_later,
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


def mark_seen(user: User, request: Request, read_at: datetime) -> None:
    """The person has opened the request: messages up to `read_at`, when the page was read,
    are no longer new to them. A message written while the page was being built stays new.

    Takes a request the caller has already read through `requests_for`.
    """
    RequestVisit.objects.update_or_create(request=request, user=user, defaults={"seen_at": read_at})


def _mark_handled_so_far(request: Request) -> None:
    """The PM has dealt with everything written so far. Stores the time of the latest
    message, not "now": a client message that lands while the PM clicks stays unhandled."""
    latest = request.messages.aggregate(at=Max("created_at"))["at"]
    request.pm_handled_at = latest or request.created_at
    request.save(update_fields=["pm_handled_at"])


@transaction.atomic
def move_to_later(pm: User, request: Request) -> Request:
    """Accepted, but not planned: the client sees it in Later, and it leaves Needs you,
    with any reply that was waiting."""
    request = Request.objects.select_for_update().get(pk=request.pk)
    if not can_move_to_later(pm, request):
        raise NotAllowedError
    _mark_handled_so_far(request)
    return _set_status(request, ClientStatus.LATER, pm)


@transaction.atomic
def create_jira_task(
    pm: User,
    request: Request,
    *,
    issue_type: str,
    summary: str,
    description: str,
    reply: str,
    attach: Sequence[Attachment] = (),
    share: Sequence[Attachment] = (),
) -> Request:
    """Creates the task in the client's Jira project and plans the request (PRD: Jira sync,
    "Create task"). The thread gets "Planned · FI-2301" and, if written, the PM's reply.

    The row stays locked while Jira answers, so a double click waits and then finds the task
    already made instead of creating a second one. Tracker problems (TrackerError) leave the
    request as it was.
    """
    request = Request.objects.select_for_update().select_related("company").get(pk=request.pk)
    if not can_create_jira_task(pm, request):
        raise NotAllowedError
    tracker = current_tracker()
    if share:
        links = "\n".join(f"* [{file.name}|{public_link(file)}]" for file in share)
        description = (
            f"{description}\n\nh3. Video and voice\n{links}\n"
            "_These links play without signing in while the task is open._"
        )
    task = tracker.create_issue(
        request.company.jira_project, issue_type, summary, description, ["dimond"]
    )
    planned = _plan_with_task(pm, request, task, reply)
    for file in attach:
        _attach(tracker, pm, planned, file)
    return planned


def _attach(tracker: Tracker, pm: User, request: Request, file: Attachment) -> None:
    """One file onto the new task. The task already exists, so a file Jira refuses (too big,
    say) becomes a note for the PM instead of undoing everything."""
    with file.file.open("rb") as stored:
        content = stored.read()
    try:
        tracker.attach_file(request.jira_key, file.name, content, file.content_type)
    except TrackerError as error:
        InternalNote.objects.create(
            request=request,
            author=pm,
            text=f"Couldn't attach {file.name} to {request.jira_key}: {error} "
            "Attach it in Jira by hand.",
        )


class TaskNotLinkable(Exception):
    """The task exists but cannot be tied to this request; the message says why."""


@transaction.atomic
def link_jira_task(pm: User, request: Request, key: str, reply: str) -> Request:
    """Existing task: ties a task already in the client's Jira project to the request and
    plans it. Nothing is written to Jira (PRD: Existing task; ADR 0010).

    Raises IssueNotFound for a key Jira does not know and TaskNotLinkable for a task in
    another project, a finished one or one that belongs to another request.
    """
    request = Request.objects.select_for_update().select_related("company").get(pk=request.pk)
    if not can_create_jira_task(pm, request):
        raise NotAllowedError
    task = current_tracker().issue(key.strip().upper())
    project = request.company.jira_project
    if task.project_key != project:
        raise TaskNotLinkable(f"{task.key} is not in {request.company}’s project ({project}).")
    if task.done:
        raise TaskNotLinkable(f"{task.key} is already done in Jira.")
    if Request.objects.filter(jira_key=task.key).exists():
        raise TaskNotLinkable(f"{task.key} already belongs to another request.")
    return _plan_with_task(pm, request, task, reply)


def _plan_with_task(pm: User, request: Request, task: Issue, reply: str) -> Request:
    """The request now follows this task: its key on the card, "Planned · FI-2301" in the
    thread, the PM's reply if written, and it leaves the PM's Needs you."""
    request.jira_key, request.jira_url = task.key, task.url
    try:
        # Two PMs linking the same task at the same moment both pass the check above; the
        # unique key lets one through, and the other hears why instead of seeing an error page.
        with transaction.atomic():
            request.save(update_fields=["jira_key", "jira_url", "updated_at"])
    except IntegrityError:
        raise TaskNotLinkable(f"{task.key} already belongs to another request.") from None
    _set_status(request, ClientStatus.PLANNED, pm)
    if reply:
        Message.objects.create(request=request, author=pm, text=reply)
    _mark_handled_so_far(request)
    return request


@transaction.atomic
def bring_back(pm: User, request: Request) -> Request:
    """Takes a request out of Later, back to Received, where the PM plans it. The PM chose
    it, so it does not come back as New."""
    request = Request.objects.select_for_update().get(pk=request.pk)
    if not can_bring_back(pm, request):
        raise NotAllowedError
    _mark_handled_so_far(request)
    return _set_status(request, ClientStatus.RECEIVED, pm)


@transaction.atomic
def decline_request(pm: User, request: Request, reason: str) -> Request:
    """Closes the request as Declined. The reason goes into the thread as the PM's message,
    so the client reads why and can answer. A Jira task, if any, stays as it is (ADR 0010).
    """
    request = Request.objects.select_for_update().get(pk=request.pk)
    if not can_decline(pm, request):
        raise NotAllowedError
    if not reason.strip():
        raise ValueError("A reason is required to decline a request.")
    Message.objects.create(request=request, author=pm, text=reason.strip())
    return _set_status(request, ClientStatus.DECLINED, pm)


@transaction.atomic
def mark_handled(pm: User, request: Request) -> None:
    """ "No reply needed": the client's latest message leaves the PM's Needs you without an
    answer. The next client message brings the request back."""
    request = Request.objects.select_for_update().get(pk=request.pk)
    if not can_mark_handled(pm, request):
        raise NotAllowedError
    _mark_handled_so_far(request)


@transaction.atomic
def add_note(pm: User, request: Request, text: str) -> InternalNote:
    """An internal note: only the PM reads it, the client and Jira never get it. Writing one
    counts as dealing with the request, so a waiting reply leaves Needs you."""
    request = Request.objects.select_for_update().get(pk=request.pk)
    if not can_add_note(pm, request):
        raise NotAllowedError
    if not text.strip():
        raise ValueError("A note needs some text.")
    note = InternalNote.objects.create(request=request, author=pm, text=text.strip())
    _mark_handled_so_far(request)
    return note
