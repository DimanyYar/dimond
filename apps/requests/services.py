import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.files.models import Attachment, JiraCopy
from apps.files.services import attach_files, public_link, removable_files, remove_files
from apps.files.uploads import CheckedFile
from apps.notifications.services import queue_reply, queue_task_topic, queue_withdrawal
from apps.tracker.base import (
    CLIENT_TAG,
    Comment,
    Issue,
    Release,
    Sprint,
    Tracker,
    TrackerError,
    TrackerRefused,
    TrackerUnreachable,
)
from apps.tracker.connection import current_tracker, jira_login_paused
from apps.tracker.jira_markup import without_tag

from .models import (
    CLOSED_STATUSES,
    JIRA_STATUSES,
    REQUEST_FIELDS,
    TASK_PROGRESS,
    ClientStatus,
    InternalNote,
    Message,
    PlanEvent,
    Request,
    RequestRevision,
    RequestType,
    RequestVisit,
    StatusEvent,
    TaskEvent,
    TaskEventKind,
    WhenNeeded,
    WithdrawalRequest,
)
from .selectors import (
    can_add_note,
    can_answer_team,
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
    can_send_to_jira,
    can_unlink_task,
    can_withdraw_now,
    open_team_questions,
    questions_answered_by,
    status_before_question,
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
    "Needs your input". Any client's reply to it hands the request back to the PM in the status
    it had before, Received (PRD: the client answers "Needs info" and the request returns to
    the PM by itself).
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
    if author.role == Role.CLIENT:
        queue_reply(message)
    if ask_for_input:
        _set_status(request, ClientStatus.NEEDS_INPUT, author)
    elif (
        author.role == Role.CLIENT
        and request.status == ClientStatus.NEEDS_INPUT
        and not open_team_questions(request)
    ):
        # A message to Dimon does not answer the team: that answer has its own form, since
        # only it goes to Jira.
        _set_status(request, status_before_question(request), author)
    return message


def answer_team(
    client: User, request: Request, text: str, files: Sequence[CheckedFile] = ()
) -> Message:
    """The client's "Send answer" under the team's question. One answer covers every open
    question, the request goes back to where it was when the team asked, and the answer goes
    to the Jira task as a comment straight away (PRD: Jira sync, team questions).

    The answer is saved before Jira is asked, so Jira failing never loses it: the PM sees why
    it did not get there and sends it again."""
    message = _save_answer(client, request, text, files)
    _pass_to_jira(message, sender=client)
    return message


@transaction.atomic
def _save_answer(
    client: User, request: Request, text: str, files: Sequence[CheckedFile]
) -> Message:
    request = Request.objects.select_for_update().get(pk=request.pk)
    if not can_answer_team(client, request):
        raise NotAllowedError
    message = Message.objects.create(request=request, author=client, text=text, answers_team=True)
    attach_files(request, client, files, message=message)
    queue_reply(message)
    _set_status(request, status_before_question(request), client)
    return message


@transaction.atomic
def send_to_jira(pm: User, message: Message) -> Message:
    """The PM's "Send to Jira" on a message of the thread, theirs or the client's, and "Send
    again" on an answer to the team that did not get there (PRD: Jira sync, team questions).
    A message already in Jira is not sent twice; the team's own questions came from there.

    The message stays locked while Jira answers, so a double click waits and then finds it
    already sent instead of adding a second comment."""
    message = Message.objects.select_for_update().select_related("request").get(pk=message.pk)
    if not can_send_to_jira(pm, message.request) or message.jira_comment_id:
        raise NotAllowedError
    return _pass_to_jira(message, sender=pm)


def _pass_to_jira(message: Message, sender: User) -> Message:
    """Add the message to the request's task as a comment. Its files go as everywhere else in
    Jira (PRD: attachments to Jira): pictures, PDFs and text uploaded to the task, video and
    voice as public links. Records the comment's id and when, or why it did not get there.

    The files go first and the comment once Jira has answered for each. A file Jira refuses for
    good (over its size limit, say) is left out and named in the comment, and the PM gets a
    note to attach it by hand, as when a task is created; sending again then skips it and the
    files already in Jira."""
    request, author = message.request, message.author
    if author is None:
        # The team's own question: it came from Jira.
        raise NotAllowedError
    error = ""
    if not request.jira_key:
        error = "The request has no Jira task now."
    elif jira_login_paused():
        # Another try would spend one more of Jira's password attempts (ADR 0019).
        error = "Jira rejected dimond's login and the sync is paused. See Logs."
    else:
        error = _send(message, author, sender)
    message.jira_error = error
    message.jira_sent_at = None if error else timezone.now()
    message.save(update_fields=["jira_comment_id", "jira_error", "jira_sent_at"])
    return message


def _send(message: Message, author: User, sender: User) -> str:
    """Uploads the files, then adds the comment, written once Jira has answered for each file
    so it can name the ones Jira refused. Returns why it stopped, or "" when it all got
    there."""
    key = message.request.jira_key
    sending = "the comment"
    try:
        # Inside the try: Jira chosen in the admin with an incomplete .env is a reason to show
        # the PM too.
        tracker = current_tracker()
        for file in message.attachments.filter(jira_copy=JiraCopy.NONE):
            if file.kind in ("video", "audio"):
                continue
            sending = file.name
            _upload(tracker, message, file, sender)
        sending = "the comment"
        body = (
            answer_comment(message, sender)
            if message.answers_team
            else _message_comment(message, author, sender)
        )
        message.jira_comment_id = tracker.add_comment(key, body)
    except TrackerUnreachable as error:
        return (
            f"{sending}: {error} It may have reached {key} anyway: check the task before "
            "sending it again."
        )
    except (TrackerError, OSError) as error:
        return f"{sending}: {error}"
    return ""


def _upload(tracker: Tracker, message: Message, file: Attachment, sender: User) -> None:
    key = message.request.jira_key
    with file.file.open("rb") as stored:
        content = stored.read()
    try:
        tracker.attach_file(key, file.name, content, file.content_type)
    except TrackerRefused as error:
        file.jira_copy = JiraCopy.REFUSED
        what = "the answer" if message.answers_team else "the message"
        InternalNote.objects.create(
            request=message.request,
            author=sender if sender.role == Role.PM else None,
            text=f"Couldn't attach {file.name} from {what} to {key}: {error} "
            "Attach it in Jira by hand.",
        )
    else:
        file.jira_copy = JiraCopy.UPLOADED
    file.save(update_fields=["jira_copy"])


# Jira's wiki markup reads these as links, mentions, macros, pictures and table cells; a
# client's words are text, and must not mention people or make a new #client question. A
# backslash goes as its character code: in Jira a doubled backslash is a line break (checked on
# j.shakuro.com, Oct 20, 2026).
JIRA_MARKUP = re.compile(r"([\[\]{}!|])")
CLIENT_TAG_TYPED = re.compile(r"#(client)", re.IGNORECASE)


def _jira_text(text: str) -> str:
    text = text.replace("\\", "&#92;")
    return CLIENT_TAG_TYPED.sub(r"\\#\1", JIRA_MARKUP.sub(r"\\\1", text))


def answer_comment(answer: Message, sender: User) -> str:
    """The answer in Jira's markup: each question quoted (the #client tag inside a quote is
    never read as a new question), who asked mentioned, then the answer and where it came
    from."""
    request = answer.request
    questions = questions_answered_by(sender, answer)
    lines = [f"{{quote}}{_jira_text(q.text)}{{quote}}" for q in questions]
    logins = dict.fromkeys(q.team_author_login for q in questions if q.team_author_login)
    mentions = " ".join(f"[~{login}]" for login in logins)
    lines.append(f"{mentions} {answer.author} ({request.company}) answered via dimond:".strip())
    return "\n".join(lines + _text_and_files(answer))


def _message_comment(message: Message, author: User, sender: User) -> str:
    """A thread message the PM sends to the task: who wrote it, and who sent it there when it
    is the client's (decided Oct 21, 2026)."""
    if author.role == Role.PM:
        signed = f"{author} (PM) via dimond:"
    else:
        signed = f"{author} ({author.company}) via dimond, sent by {sender}:"
    return "\n".join([signed, *_text_and_files(message)])


def _text_and_files(message: Message) -> list[str]:
    request = message.request
    lines = [_jira_text(message.text)] if message.text else []
    lines += [_jira_file(file) for file in message.attachments.all()]
    lines.append(f"[{request.key}|{settings.SITE_URL}{request.get_absolute_url()}]")
    return lines


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
    ask = WithdrawalRequest.objects.create(request=request, requested_by=client, reason=message)
    # The reason goes to Discord with the ask, as one post, not as a reply of its own.
    queue_withdrawal(ask)
    return ask


@transaction.atomic
def decide_withdrawal(pm: User, request: Request, *, confirm: bool) -> Request:
    """The PM's answer to a client's ask: withdraw the request, or keep it going.

    dimond leaves the Jira task alone (ADR 0010); closing it there is the PM's own step.
    """
    request = Request.objects.select_for_update().get(pk=request.pk)
    if not can_decide_withdrawal(pm, request):
        raise NotAllowedError
    ask = WithdrawalRequest.objects.get(request=request, confirmed__isnull=True)
    ask.confirmed, ask.decided_by, ask.decided_at = confirm, pm, timezone.now()
    ask.save(update_fields=["confirmed", "decided_by", "decided_at"])
    queue_withdrawal(ask)
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
    sprint: Sprint | None = None,
) -> Request:
    """Creates the task in the client's Jira project, in `sprint` or the backlog, and plans
    the request (PRD: Jira sync, "Create task"). The thread gets "Planned · FI-2301" and, if
    written, the PM's reply.

    The row stays locked while Jira answers, so a double click waits and then finds the task
    already made instead of creating a second one. Tracker problems (TrackerError) leave the
    request as it was.
    """
    request = Request.objects.select_for_update().select_related("company").get(pk=request.pk)
    if not can_create_jira_task(pm, request):
        raise NotAllowedError
    tracker = current_tracker()
    if share:
        links = "\n".join(f"* [{_link_text(file.name)}|{public_link(file)}]" for file in share)
        description = (
            f"{description}\n\nh3. Video and voice\n{links}\n"
            "_These links play without signing in while the request is open in dimond._"
        )
    task = tracker.create_issue(
        request.company.jira_project, issue_type, summary, description, ["dimond"]
    )
    planned = _plan_with_task(pm, request, task, reply)
    if sprint is not None:
        _move_to_sprint(tracker, pm, planned, sprint)
    for file in attach:
        _attach(tracker, pm, planned, file)
    return planned


def _move_to_sprint(tracker: Tracker, pm: User, request: Request, sprint: Sprint) -> None:
    """The task already exists, so a sprint Jira refuses (closed a moment ago, say) leaves it
    in the backlog with a note for the PM instead of undoing everything."""
    try:
        tracker.move_to_sprint(request.jira_key, sprint.id)
    except TrackerUnreachable as error:
        # The connection broke, maybe after Jira moved the task.
        InternalNote.objects.create(
            request=request,
            author=pm,
            text=f"Couldn't confirm that {request.jira_key} reached sprint {sprint.name}: "
            f"{error} Check its sprint in Jira.",
        )
    except TrackerError as error:
        InternalNote.objects.create(
            request=request,
            author=pm,
            text=f"Couldn't put {request.jira_key} into sprint {sprint.name}: {error} "
            "It is in the backlog; move it in Jira by hand.",
        )


def _attach(tracker: Tracker, pm: User, request: Request, file: Attachment) -> bool:
    """One file onto the task; True when it got there. The task already exists, so a file
    Jira refuses (too big, say) becomes a note for the PM instead of undoing everything."""
    try:
        with file.file.open("rb") as stored:
            content = stored.read()
        tracker.attach_file(request.jira_key, file.name, content, file.content_type)
    except (TrackerError, OSError) as error:
        InternalNote.objects.create(
            request=request,
            author=pm,
            text=f"Couldn't attach {file.name} to {request.jira_key}: {error} "
            "Attach it in Jira by hand.",
        )
        return False
    return True


def _jira_file(file: Attachment) -> str:
    """A file of the message in its comment: shown or linked once uploaded, or a public link
    that plays video and voice without signing in."""
    if file.kind in ("video", "audio"):
        return f"[{_link_text(file.name)}|{public_link(file)}]"
    if file.jira_copy == JiraCopy.REFUSED:
        return f"{_link_text(file.name)} (Jira didn't take this file; it is in the request)"
    return _attachment_markup(file)


def _link_text(name: str) -> str:
    # "[", "]" and "|" are link syntax in Jira markup, and a client's file name may hold them.
    return name.translate(str.maketrans("[]|", "()/"))


def _attachment_markup(file: Attachment) -> str:
    # Jira finds an attachment by its exact name, and a name holding markup characters
    # cannot be written as a reference, so such a file is only named.
    if set(file.name) & set("[]|!"):
        return _link_text(file.name)
    return f"!{file.name}|thumbnail!" if file.kind == "image" else f"[^{file.name}]"


class TaskNotLinkable(Exception):
    """The task exists but cannot be tied to this request; the message says why."""


def link_jira_task(
    pm: User,
    request: Request,
    key: str,
    reply: str,
    *,
    comment: bool = False,
    attach: Sequence[Attachment] = (),
    share: Sequence[Attachment] = (),
) -> Request:
    """Existing task: ties a task already in the client's Jira project to the request and
    plans it (PRD: Existing task). With `comment`, the task gets one comment pointing back to
    the request, with the chosen files: pictures and PDFs uploaded, video and voice as public
    links. The description is never touched (ADR 0010); without `comment` nothing reaches Jira.

    Raises IssueNotFound for a key Jira does not know and TaskNotLinkable for a task in
    another project, a finished one or one that belongs to another request.
    """
    planned = _link_task(pm, request, key, reply)
    # The link is saved first: a database rollback cannot take a file back out of Jira, so
    # whatever goes wrong while writing to Jira becomes a note and the link stays.
    if comment:
        tracker = current_tracker()
        links = [(file, public_link(file)) for file in share]
        attached = [file for file in attach if _attach(tracker, pm, planned, file)]
        _comment_with_files(tracker, pm, planned, attached, links)
    return planned


@transaction.atomic
def _link_task(pm: User, request: Request, key: str, reply: str) -> Request:
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


def _comment_with_files(
    tracker: Tracker,
    pm: User,
    request: Request,
    attached: Sequence[Attachment],
    links: Sequence[tuple[Attachment, str]],
) -> None:
    """One comment on the linked task, in Jira's markup: where the request lives, the
    pictures shown inline, other uploaded files and the public links."""
    lines = [
        f"From dimond: [{request.key}|{settings.SITE_URL}{request.get_absolute_url()}] · "
        f"{request.title} ({request.author}, {request.company})"
    ]
    lines += [_attachment_markup(file) for file in attached]
    lines += [f"[{_link_text(file.name)}|{link}]" for file, link in links]
    try:
        tracker.add_comment(request.jira_key, "\n".join(lines))
    except TrackerUnreachable as error:
        # Jira may have saved the comment and lost only its answer.
        InternalNote.objects.create(
            request=request,
            author=pm,
            text=f"The comment may not have reached {request.jira_key}: {error} "
            "Check the task in Jira before adding it by hand.",
        )
    except TrackerError as error:
        InternalNote.objects.create(
            request=request,
            author=pm,
            text=f"Couldn't add the comment to {request.jira_key}: {error}",
        )


def _plan_with_task(pm: User, request: Request, task: Issue, reply: str) -> Request:
    """The request now follows this task: its key on the card, "Planned · FI-2301" in the
    thread, the PM's reply if written, and it leaves the PM's Needs you."""
    request.jira_key, request.jira_url, request.jira_status = task.key, task.url, task.status
    # A new task replaces one the sync lost (deleted, or moved to another client's project);
    # its release dates come with the next sync.
    request.lost_jira_key = request.jira_moved_to = ""
    request.planned_releases = []
    # A new task gets a topic of its own in Discord, named after its key.
    request.discord_topic = ""
    try:
        # Two PMs linking the same task at the same moment both pass the check above; the
        # unique key lets one through, and the other hears why instead of seeing an error page.
        with transaction.atomic():
            request.save(
                update_fields=[
                    "jira_key",
                    "jira_url",
                    "jira_status",
                    "lost_jira_key",
                    "jira_moved_to",
                    "planned_releases",
                    "discord_topic",
                    "updated_at",
                ]
            )
    except IntegrityError:
        raise TaskNotLinkable(f"{task.key} already belongs to another request.") from None
    _set_status(request, ClientStatus.PLANNED, pm)
    TaskEvent.objects.create(
        request=request, kind=TaskEventKind.LINKED, task_key=task.key, actor=pm
    )
    queue_task_topic(request)
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


@dataclass(frozen=True)
class TaskFollowed:
    """What one sync did to one request, for the sync log."""

    request: Request
    jira_from: str
    jira_to: str
    status_from: ClientStatus
    status_to: ClientStatus
    effect: str
    unmapped: bool


@transaction.atomic
def follow_task(request: Request, jira_status: str) -> TaskFollowed | None:
    """The Jira sync's step for one request: save the task's status and move the client's
    status after it where the PRD allows (PRD: Jira sync). Returns None when nothing changed,
    so a run with no news logs nothing."""
    request = Request.objects.select_for_update().get(pk=request.pk)
    status_from = ClientStatus(request.status)
    jira_from = request.jira_status
    target = JIRA_STATUSES.get(jira_status)
    if target is not None and _sync_may_move(status_from, target):
        request = _set_status(request, target, None)
    if jira_from != jira_status:
        request.jira_status = jira_status
        request.save(update_fields=["jira_status"])
    status_to = ClientStatus(request.status)
    if jira_from == jira_status and status_from == status_to:
        return None
    return TaskFollowed(
        request=request,
        jira_from=jira_from,
        jira_to=jira_status,
        status_from=status_from,
        status_to=status_to,
        effect=_sync_effect(status_from, status_to, jira_status, target),
        unmapped=target is None,
    )


@dataclass(frozen=True)
class ReleasesFollowed:
    """The task's release dates changed, for the sync log."""

    request: Request
    shown: str  # what the client sees now, "" for none


@transaction.atomic
def follow_releases(request: Request, releases: tuple[Release, ...]) -> ReleasesFollowed | None:
    """Save the task's dated fix versions for "Planned for" on the card. A date that appears or
    moves gets a line in the thread for the client; one taken away changes the card quietly
    (PRD: Jira sync). Returns None when nothing changed."""
    request = Request.objects.select_for_update().get(pk=request.pk)
    seen = [{"name": r.name, "date": r.release_date.isoformat()} for r in releases]
    previous = request.planned_releases
    if seen == previous:
        return None
    before = {release["name"]: release["date"] for release in previous}
    # Only a date that is new or different is news; one version taken off the list is not.
    news = any(before.get(release["name"]) != release["date"] for release in seen)
    request.planned_releases = seen
    request.save(update_fields=["planned_releases"])
    if news:
        PlanEvent.objects.create(request=request, releases=seen, moved=bool(previous))
    return ReleasesFollowed(request=request, shown=request.planned_for)


def _sync_may_move(current: ClientStatus, target: ClientStatus) -> bool:
    if current in CLOSED_STATUSES or current == ClientStatus.NEEDS_INPUT:
        # Closed stays closed, and a question to the client waits for their answer.
        return False
    if current == ClientStatus.RECEIVED:
        # Only a request whose client has just answered a question has a task and Received.
        return True
    if current == ClientStatus.LATER:
        # Parked by the PM: it comes out once the team takes the task into work.
        return TASK_PROGRESS.index(target) > TASK_PROGRESS.index(ClientStatus.PLANNED)
    return TASK_PROGRESS.index(target) > TASK_PROGRESS.index(current)


def _sync_effect(
    before: ClientStatus, after: ClientStatus, jira_status: str, target: ClientStatus | None
) -> str:
    if before != after:
        closed = " The request is closed." if after in CLOSED_STATUSES else ""
        return f"Client status: {before.label} → {after.label}.{closed}"
    if target is None:
        return f"“{jira_status}” isn't mapped, so the client keeps “{before.label}”."
    if before == ClientStatus.DONE and target != ClientStatus.DONE:
        return "Reopened in Jira; the request stays closed."
    if before in CLOSED_STATUSES:
        return f"The request is {before.label.lower()} in dimond; Jira goes its own way."
    if before == ClientStatus.NEEDS_INPUT:
        return "Waits for the client's answer first."
    if before == ClientStatus.LATER:
        return "Stays in Later until the team takes the task into work."
    if target != before:
        return f"Went back in Jira; the client keeps seeing “{before.label}”."
    return f"The client still sees “{before.label}”."


@dataclass(frozen=True)
class QuestionFollowed:
    """A team question the sync brought into a request, or an edit of one, for the sync log."""

    request: Request
    message: Message
    edited: bool
    status_from: ClientStatus


def _question_text(comment: Comment) -> str:
    """The question as the client reads it: the tag is for the team, not for them."""
    without_tag = CLIENT_TAG.sub("", comment.text)
    return "\n".join(
        re.sub(r"[ \t]{2,}", " ", line).strip() for line in without_tag.split("\n")
    ).strip()


def _question_markup(comment: Comment) -> str:
    # Without the tag a line starting with it is not read as a numbered list ("# item").
    return without_tag(comment.markup, CLIENT_TAG)


@transaction.atomic
def add_team_question(
    request: Request,
    comment: Comment,
    files: Sequence[CheckedFile],
    files_not_shown: Sequence[str],
) -> QuestionFollowed:
    """A comment tagged #client becomes the team's message to the client, from "Team", and
    the request waits for their answer, whatever its status: a closed request opens again
    (PRD: Jira sync, team questions). The PM sees it as a new message, not in Needs you."""
    request = Request.objects.select_for_update().get(pk=request.pk)
    message = Message.objects.create(
        request=request,
        author=None,
        text=_question_text(comment),
        team_comment_id=comment.id,
        team_author=comment.author_name,
        team_author_login=comment.author_login,
        team_edited_at=comment.updated,
        team_markup=_question_markup(comment),
        files_not_shown=list(files_not_shown),
    )
    attach_files(request, None, files, message=message)
    status_from = ClientStatus(request.status)
    request = _set_status(request, ClientStatus.NEEDS_INPUT, None)
    return QuestionFollowed(request, message, edited=False, status_from=status_from)


@transaction.atomic
def edit_team_question(request: Request, comment: Comment) -> QuestionFollowed:
    """The team reworded the question in Jira: the client reads the new words. Files stay as
    they were copied; the status does not change."""
    message = Message.objects.select_for_update().get(request=request, team_comment_id=comment.id)
    message.text, message.team_edited_at = _question_text(comment), comment.updated
    message.team_markup = _question_markup(comment)
    message.save(update_fields=["text", "team_edited_at", "team_markup"])
    return QuestionFollowed(request, message, edited=True, status_from=ClientStatus(request.status))


def keep_team_markup(request: Request, comment: Comment) -> None:
    """A question copied before dimond kept Jira's markup gets it from the unchanged comment,
    so the thread formats it. Nothing else changes: it is not an edit, the client is not told
    (decided Oct 27, 2026)."""
    Message.objects.filter(request=request, team_comment_id=comment.id).update(
        team_markup=_question_markup(comment)
    )


@dataclass(frozen=True)
class TaskLost:
    """The sync could not follow a request's task any more, for the sync log."""

    request: Request
    task_key: str
    what: str
    effect: str
    needs_attention: bool


@transaction.atomic
def drop_deleted_task(request: Request) -> TaskLost:
    """The task is gone from Jira (deleted, or dimond's account no longer sees it): the sync
    unlinks it, keeps the client's status, and tells the PM in the thread; the request waits in
    the PM's Needs you as "Task deleted" until they link another task or decline it."""
    request = Request.objects.select_for_update().get(pk=request.pk)
    key = request.jira_key
    status = ClientStatus(request.status).label
    request.lost_jira_key, request.jira_key, request.jira_url, request.jira_status = key, "", "", ""
    # The task's release dates went with it.
    request.planned_releases = []
    request.save(
        update_fields=["lost_jira_key", "jira_key", "jira_url", "jira_status", "planned_releases"]
    )
    TaskEvent.objects.create(request=request, kind=TaskEventKind.DELETED, task_key=key)
    return TaskLost(
        request=request,
        task_key=key,
        what="Not found in Jira",
        effect=f"Deleted, or dimond’s account can’t see it. Unlinked; the client keeps seeing "
        f"“{status}”.",
        needs_attention=True,
    )


@transaction.atomic
def follow_moved_task(request: Request, task: Issue) -> TaskLost:
    """Jira moved the task and answers under its new key. Into the client's own project the
    request follows the new key quietly; into another client's project the sync stops: that
    task is no longer this client's work (PRD: Jira sync)."""
    request = Request.objects.select_for_update().select_related("company").get(pk=request.pk)
    old = request.jira_key
    status = ClientStatus(request.status).label
    own_project = task.project_key == request.company.jira_project
    taken = Request.objects.filter(jira_key=task.key).exclude(pk=request.pk).exists()
    if own_project and not taken:
        request.jira_key, request.jira_url = task.key, task.url
        request.save(update_fields=["jira_key", "jira_url"])
        TaskEvent.objects.create(
            request=request, kind=TaskEventKind.MOVED, task_key=old, new_key=task.key
        )
        return TaskLost(
            request=request,
            task_key=task.key,
            what=f"Moved in Jira: {old} → {task.key}",
            effect=f"The request now follows {task.key}.",
            needs_attention=False,
        )
    request.jira_moved_to = task.key
    request.planned_releases = []
    request.save(update_fields=["jira_moved_to", "planned_releases"])
    TaskEvent.objects.create(
        request=request, kind=TaskEventKind.MOVED_AWAY, task_key=old, new_key=task.key
    )
    why = (
        f"{task.key} already belongs to another request"
        if own_project
        else f"{task.key} is outside {request.company}’s project"
    )
    return TaskLost(
        request=request,
        task_key=old,
        what=f"Moved to {task.key}",
        effect=f"{why}, so the sync no longer follows it; the client keeps seeing “{status}”.",
        needs_attention=True,
    )


@transaction.atomic
def unlink_task(pm: User, request: Request) -> Request:
    """Unlink, from Logs: the PM lets go of a task Jira moved into another client's project.
    The request keeps its status; the PM links another task or declines it."""
    request = Request.objects.select_for_update().get(pk=request.pk)
    if not can_unlink_task(pm, request):
        raise NotAllowedError
    TaskEvent.objects.create(
        request=request,
        kind=TaskEventKind.UNLINKED,
        task_key=request.jira_key,
        new_key=request.jira_moved_to,
        actor=pm,
    )
    request.jira_key = request.jira_url = request.jira_status = request.jira_moved_to = ""
    request.planned_releases = []
    request.save(
        update_fields=["jira_key", "jira_url", "jira_status", "jira_moved_to", "planned_releases"]
    )
    return request
