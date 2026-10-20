import logging
import secrets
from collections.abc import Collection, Iterable
from datetime import datetime, timedelta
from uuid import UUID

from django.core.files.base import ContentFile

from apps.accounts.models import User
from apps.requests.models import Message, Request, RequestRevision

from . import previews
from .models import Attachment, PreviewStatus
from .uploads import CheckedFile

log = logging.getLogger(__name__)

EXCERPT_LINES = 6
EXCERPT_WIDTH = 80


def _excerpt(checked: CheckedFile) -> str:
    """A text file's first lines, cut to what a tile can show."""
    checked.file.seek(0)
    head = checked.file.read(4096)
    checked.file.seek(0)
    lines = head.decode("utf-8-sig", errors="replace").splitlines()[:EXCERPT_LINES]
    return "\n".join(line[:EXCERPT_WIDTH] for line in lines)


def _preview_status(checked: CheckedFile) -> PreviewStatus:
    if checked.content_type.startswith("image/") or checked.content_type == "application/pdf":
        return PreviewStatus.PENDING
    return PreviewStatus.PENDING if checked.poster else PreviewStatus.NONE


def attach_files(
    request: Request,
    uploader: User | None,
    files: Iterable[CheckedFile],
    *,
    message: Message | None = None,
    added_in: RequestRevision | None = None,
) -> list[Attachment]:
    """Save checked files to the request, to one of its replies or as part of an edit; with no
    uploader, the files of a team question the Jira sync copied. Called
    from inside a request action that has already decided this person may add files, so the
    files and the action are saved or rolled back together."""
    return [
        Attachment.objects.create(
            request=request,
            message=message,
            added_in=added_in,
            uploaded_by=uploader,
            file=checked.file,
            name=checked.name,
            content_type=checked.content_type,
            size=checked.size,
            preview_status=_preview_status(checked),
            # The video's frame waits here for the worker, which replaces it with the preview.
            preview=ContentFile(checked.poster, name="poster.jpg") if checked.poster else "",
            excerpt=_excerpt(checked) if checked.content_type == "text/plain" else "",
        )
        for checked in files
    ]


def removable_files(request: Request, ids: Collection[UUID]) -> list[Attachment]:
    """Which of these files an edit can take off the request: the request's own files still
    on the card. Files from the thread stay, like the messages they came with."""
    return list(request.attachments.shown().filter(message__isnull=True, id__in=ids))


def remove_files(files: Iterable[Attachment], removed_in: RequestRevision) -> None:
    """Take files off the card as part of an edit. The files stay on disk and in the edit
    history, so the PM can still see what was there."""
    Attachment.objects.filter(id__in=[file.id for file in files]).update(removed_in=removed_in)


def make_preview(attachment: Attachment) -> None:
    """Draw the small picture a tile shows: an image made smaller, a PDF's first page, a video's
    frame the browser sent. A file that cannot be read keeps its tile without a picture; the
    file itself still opens. Storage errors are left to the caller: the file is fine, so the
    preview waits for another try."""
    is_video = attachment.kind == "video"
    source = attachment.preview if is_video else attachment.file
    with source.open("rb") as stored:
        content = stored.read()
    # A video's frame is deleted only once the database no longer points at it, so a failure
    # in between leaves the attachment pending with its frame, ready for the next try.
    frame = attachment.preview.name if is_video else ""
    try:
        preview = previews.draw(content, "image/jpeg" if is_video else attachment.content_type)
    except previews.CANNOT_READ:
        log.warning("No preview for attachment %s", attachment.id, exc_info=True)
        attachment.preview = ""
        attachment.preview_status = PreviewStatus.NONE
        attachment.save(update_fields=["preview", "preview_status"])
    else:
        attachment.preview.save("preview.webp", ContentFile(preview.webp), save=False)
        attachment.pages = preview.pages
        attachment.preview_status = PreviewStatus.READY
        attachment.save(update_fields=["preview", "preview_status", "pages"])
    if frame:
        attachment.preview.storage.delete(frame)


def make_pending_previews(limit: int = 20) -> int:
    """Make previews waiting since upload; run by the worker (ADR 0004). Returns how many."""
    pending = list(Attachment.objects.filter(preview_status=PreviewStatus.PENDING)[:limit])
    for attachment in pending:
        try:
            make_preview(attachment)
        except OSError:
            # Storage out of reach for a moment: the file stays pending for the next run, and
            # the files after it still get their previews now.
            log.warning("Preview postponed for attachment %s", attachment.id, exc_info=True)
    return len(pending)


# A manual share is for a look after the request closed: a week, then it closes again.
SHARE_AGAIN_FOR = timedelta(days=7)


def public_link(attachment: Attachment) -> str:
    """The address that plays the file without signing in (PRD: attachments to Jira). The
    secret part is made once and kept, so links already in Jira keep working."""
    if not attachment.public_token:
        attachment.public_token = secrets.token_urlsafe(32)
        attachment.save(update_fields=["public_token"])
    return attachment.public_url


def share_again(attachment: Attachment, now: datetime) -> Attachment:
    """Opens a closed request's public link for another week. The caller checks it is the
    client's PM (`can_share_again`)."""
    attachment.shared_until = now + SHARE_AGAIN_FOR
    attachment.save(update_fields=["shared_until"])
    return attachment
