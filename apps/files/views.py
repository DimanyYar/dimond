import re
from collections.abc import Iterator
from typing import cast
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBase,
    StreamingHttpResponse,
)
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.utils.http import content_disposition_header
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST, require_safe

from apps.accounts.models import User

from . import selectors, services
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


def _bytes_of(attachment: Attachment, first: int, last: int) -> Iterator[bytes]:
    """The file from byte `first` to `last`, a piece at a time: a player often asks for the
    whole 20 MB video at once, and it should not sit in the server's memory."""
    with attachment.file.open("rb") as stored:
        stored.seek(first)
        left = last - first + 1
        while left > 0:
            piece = stored.read(min(left, FileResponse.block_size))
            # A file shorter on disk than recorded ends here instead of looping forever.
            if not piece:
                return
            left -= len(piece)
            yield piece


def _part_of(attachment: Attachment, header: str, as_attachment: bool) -> HttpResponseBase:
    """A piece of a video or audio file: browsers fetch these while playing, and Safari does not
    play at all from a server that cannot send one."""
    size = attachment.size
    wanted = _byte_range(header, size)
    if wanted is None:
        refused = HttpResponse(status=416)
        refused["Content-Range"] = f"bytes */{size}"
        return refused
    first, last = wanted
    response = StreamingHttpResponse(
        _bytes_of(attachment, first, last), status=206, content_type=attachment.content_type
    )
    response["Content-Length"] = str(last - first + 1)
    response["Content-Range"] = f"bytes {first}-{last}/{size}"
    # Only None for a file without a name, and every attachment has one.
    response["Content-Disposition"] = cast(
        str, content_disposition_header(as_attachment, attachment.name)
    )
    return response


def _send(request: HttpRequest, attachment: Attachment) -> HttpResponseBase:
    """The file itself, or the piece of it a player asks for."""
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
    return _send(request, attachment)


@require_safe
@login_required
def open_preview(request: HttpRequest, attachment_id: UUID) -> HttpResponseBase:
    """The small picture on a file's tile, for the same people as the file itself."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    try:
        attachment = selectors.attachment_for(request.user, attachment_id)
    except Attachment.DoesNotExist:
        raise Http404 from None
    if not attachment.preview_url:
        raise Http404
    response = FileResponse(attachment.preview.open("rb"), content_type="image/webp")
    # Like the file: a picture of a client's screenshot stays out of every cache.
    patch_cache_control(response, private=True, no_store=True)
    return response


@require_safe
def open_public_file(request: HttpRequest, token: str) -> HttpResponseBase:
    """A video or voice from a Jira task, for anyone with the link, no sign-in, while the link
    works (PRD: attachments to Jira). A closed or made-up link answers like a missing page."""
    try:
        attachment = selectors.public_attachment(token)
    except Attachment.DoesNotExist:
        raise Http404 from None
    response = _send(request, attachment)
    # The secret address must not leak to other sites or into search results.
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@require_POST
@login_required
def share_file_again(request: HttpRequest, attachment_id: UUID) -> HttpResponseBase:
    """The PM opens a closed request's public link for another week."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    try:
        attachment = selectors.attachment_for(request.user, attachment_id)
    except Attachment.DoesNotExist:
        raise Http404 from None
    now = timezone.now()
    if not selectors.can_share_again(request.user, attachment, now):
        raise PermissionDenied
    services.share_again(attachment, now)
    return redirect(f"{attachment.request.get_absolute_url()}#all-files")
