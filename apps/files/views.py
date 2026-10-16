from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpRequest
from django.utils.cache import patch_cache_control
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_safe

from apps.accounts.models import User

from . import selectors
from .models import Attachment


@require_safe
@login_required
# The card's viewer shows a PDF in a frame; every other page stays unframeable (DENY).
@xframe_options_sameorigin
def open_file(request: HttpRequest, attachment_id: UUID) -> FileResponse:
    """Send one file to someone allowed to see it. Images and PDFs open in the browser unless
    `?download` asks for a download, everything else always downloads; the checked type and
    nosniff stop the browser from guessing."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    try:
        attachment = selectors.attachment_for(request.user, attachment_id)
    except Attachment.DoesNotExist:
        raise Http404 from None
    response = FileResponse(
        attachment.file.open("rb"),
        content_type=attachment.content_type,
        as_attachment=not attachment.shown_in_browser or "download" in request.GET,
        filename=attachment.name,
    )
    # Client files stay in no cache: not in a shared proxy, and not in a browser two people
    # share, where the next person would get the file without the access check.
    patch_cache_control(response, private=True, no_store=True)
    return response
