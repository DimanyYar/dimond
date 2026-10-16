from collections.abc import Iterable

from apps.accounts.models import User
from apps.requests.models import Request

from .models import Attachment
from .uploads import CheckedFile


def attach_files(
    request: Request, uploader: User, files: Iterable[CheckedFile]
) -> list[Attachment]:
    """Save checked files to the request. Called from inside a request action (creating a
    request now, replying and editing later) that has already decided this person may add
    files, so the files and the action are saved or rolled back together."""
    return [
        Attachment.objects.create(
            request=request,
            uploaded_by=uploader,
            file=checked.file,
            name=checked.name,
            content_type=checked.content_type,
            size=checked.size,
        )
        for checked in files
    ]
