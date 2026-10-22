from anymail.webhooks.resend import ResendTrackingWebhookView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import User
from apps.accounts.selectors import has_clients_page

from . import selectors, services


def _pm(request: HttpRequest) -> User:
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    if not has_clients_page(request.user):
        raise Http404
    return request.user


@require_GET
@login_required
def logs_emails(request: HttpRequest) -> HttpResponse:
    """Logs · Emails (mockup 5.5 · Logs · Emails): what dimond sent, to whom, and whether the
    mail server took it. PMs only; everyone else gets "not found", as on the other PM pages."""
    _pm(request)
    return render(
        request,
        "notifications/logs_emails.html",
        {
            "emails": selectors.emails_for(_pm(request)),
            "pause": selectors.mail_pause(),
            "today_count": selectors.sent_today(),
            "today": timezone.now().strftime("%Y%m%d"),
        },
    )


@require_POST
@login_required
def try_emails_now(request: HttpRequest) -> HttpResponse:
    """ "Try now" on paused emails: one email at once instead of at the next try."""
    _pm(request)
    if services.try_emails_now():
        messages.success(request, "That one went out: emails are going out again.")
    else:
        messages.error(request, "Still not sent. dimond keeps trying every 5 minutes.")
    return redirect("logs_emails")


_resend_reports = ResendTrackingWebhookView.as_view()


@csrf_exempt
def resend_webhook(request: HttpRequest) -> HttpResponse:
    """Resend's delivery reports (ADR 0021). anymail checks the signature and answers 400 to a
    wrong one; with no secret set the address does not exist."""
    if not settings.ANYMAIL_RESEND_SIGNING_SECRET:
        raise Http404
    response: HttpResponse = _resend_reports(request)
    return response
