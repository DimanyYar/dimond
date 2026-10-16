from collections.abc import Collection, Iterable
from uuid import UUID

from apps.accounts.models import User
from apps.requests.models import Message, Request, RequestRevision

from .models import Attachment
from .uploads import CheckedFile


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
