import re
from typing import cast
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseBase
from django.utils.cache import patch_cache_control
from django.utils.http import content_disposition_header
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_safe

from apps.accounts.models import User

from . import selectors
from .models import Attachment

# "bytes=100-199", "bytes=100-" (to the end) or "bytes=-500" (the last 500 bytes).
_ONE_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


def _byte_range(header: str, size: int) -> tuple[int, int] | None:
    """The first and last byte a Range header asks for, or None when it asks for none of the
    file. Several ranges at once are not supported: browsers ask for one."""
    match = _ONE_RANGE.fullmatch(header.strip())
    if match is None or match.groups() == ("", ""):
        return None
    first, last = match.groups()
    if first == "":
        return (max(size - int(last), 0), size - 1) if int(last) else None
    end = min(int(last), size - 1) if last else size - 1
    return (int(first), end) if int(first) <= end else None


def _part_of(attachment: Attachment, header: str, as_attachment: bool) -> HttpResponse:
    """A piece of a video or audio file: browsers fetch these while playing, and Safari does not
    play at all from a server that cannot send one."""
    size = attachment.size
    wanted = _byte_range(header, size)
    if wanted is None:
        refused = HttpResponse(status=416)
        refused["Content-Range"] = f"bytes */{size}"
        return refused
    first, last = wanted
    with attachment.file.open("rb") as stored:
        stored.seek(first)
        response = HttpResponse(
            stored.read(last - first + 1), status=206, content_type=attachment.content_type
        )
    response["Content-Range"] = f"bytes {first}-{last}/{size}"
    # Only None for a file without a name, and every attachment has one.
    response["Content-Disposition"] = cast(
        str, content_disposition_header(as_attachment, attachment.name)
    )
    return response


@require_safe
@login_required
# The card's viewer shows a PDF in a frame; every other page stays unframeable (DENY).
@xframe_options_sameorigin
def open_file(request: HttpRequest, attachment_id: UUID) -> HttpResponseBase:
    """Send one file to someone allowed to see it. Images, PDFs, video and audio open in the
    browser unless `?download` asks for a download, everything else always downloads; the
    checked type and nosniff stop the browser from guessing."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    try:
        attachment = selectors.attachment_for(request.user, attachment_id)
    except Attachment.DoesNotExist:
        raise Http404 from None
    as_attachment = not attachment.shown_in_browser or "download" in request.GET
    response: HttpResponseBase
    if "Range" in request.headers:
        response = _part_of(attachment, request.headers["Range"], as_attachment)
    else:
        response = FileResponse(
            attachment.file.open("rb"),
            content_type=attachment.content_type,
            as_attachment=as_attachment,
            filename=attachment.name,
        )
    response["Accept-Ranges"] = "bytes"
    # Client files stay in no cache: not in a shared proxy, and not in a browser two people
    # share, where the next person would get the file without the access check.
    patch_cache_control(response, private=True, no_store=True)
    return response
