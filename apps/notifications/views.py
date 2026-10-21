from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.accounts.models import User
from apps.accounts.selectors import has_clients_page

from . import selectors


@require_GET
@login_required
def logs_emails(request: HttpRequest) -> HttpResponse:
    """Logs · Emails (mockup 5.5 · Logs · Emails): what dimond sent, to whom, and whether the
    mail server took it. PMs only; everyone else gets "not found", as on the other PM pages."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    if not has_clients_page(request.user):
        raise Http404
    return render(
        request,
        "notifications/logs_emails.html",
        {
            "emails": selectors.emails_for(request.user),
            "today": timezone.now().strftime("%Y%m%d"),
        },
    )
