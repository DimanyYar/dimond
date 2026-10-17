import logging
from collections.abc import Collection, Iterable
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


def _preview_status(content_type: str) -> PreviewStatus:
    if content_type.startswith("image/") or content_type == "application/pdf":
        return PreviewStatus.PENDING
    return PreviewStatus.NONE


def attach_files(
    request: Request,
    uploader: User,
    files: Iterable[CheckedFile],
    *,
    message: Message | None = None,
    added_in: RequestRevision | None = None,
) -> list[Attachment]:
    """Save checked files to the request, to one of its replies or as part of an edit. Called
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
            preview_status=_preview_status(checked.content_type),
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
    """Draw the small picture a tile shows: an image made smaller, a PDF's first page. A file
    that cannot be read keeps its tile without a picture; the file itself still opens."""
    try:
        with attachment.file.open("rb") as stored:
            preview = previews.draw(stored.read(), attachment.content_type)
    except previews.CANNOT_READ:
        log.warning("No preview for attachment %s", attachment.id, exc_info=True)
        attachment.preview_status = PreviewStatus.NONE
        attachment.save(update_fields=["preview_status"])
        return
    attachment.preview.save("preview.webp", ContentFile(preview.webp), save=False)
    attachment.pages = preview.pages
    attachment.preview_status = PreviewStatus.READY
    attachment.save(update_fields=["preview", "preview_status", "pages"])


def make_pending_previews(limit: int = 20) -> int:
    """Make previews waiting since upload; run by the worker (ADR 0004). Returns how many."""
    pending = list(Attachment.objects.filter(preview_status=PreviewStatus.PENDING)[:limit])
    for attachment in pending:
        make_preview(attachment)
    return len(pending)
