from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpRequest
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_safe

from apps.accounts.models import User

from . import selectors
from .models import Attachment


@require_safe
@login_required
def open_file(request: HttpRequest, attachment_id: UUID) -> FileResponse:
    """Send one file to someone allowed to see it. Images and PDFs open in the browser,
    everything else downloads; the checked type and nosniff stop the browser from guessing."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    try:
        attachment = selectors.attachment_for(request.user, attachment_id)
    except Attachment.DoesNotExist:
        raise Http404 from None
    response = FileResponse(
        attachment.file.open("rb"),
        content_type=attachment.content_type,
        as_attachment=not attachment.shown_in_browser,
        filename=attachment.name,
    )
    # Client files must not stay in a shared cache (a proxy, an office gateway).
    patch_cache_control(response, private=True)
    return response
