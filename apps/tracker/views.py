import hmac

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import User
from apps.accounts.selectors import has_clients_page

from . import selectors, services


def _pm(request: HttpRequest) -> User:
    """The signed-in PM; everyone else gets "not found", as on the other PM pages."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    if not has_clients_page(request.user):
        raise Http404
    return request.user


@require_GET
@login_required
def logs(request: HttpRequest) -> HttpResponse:
    """Logs · Jira sync (mockup 4.x · PM logs): the runs, what needs attention, the mapping."""
    pm = _pm(request)
    try:
        show = selectors.RunFilter(request.GET.get("show", selectors.RunFilter.ALL))
    except ValueError:
        show = selectors.RunFilter.ALL
    return render(
        request, "tracker/logs.html", {"show": show, "page": selectors.logs_page(pm, show)}
    )


@require_POST
@login_required
def sync_now(request: HttpRequest) -> HttpResponse:
    services.ask_for_sync(_pm(request))
    messages.success(request, "Sync started. New statuses show up here in a few seconds.")
    return redirect("logs")


@csrf_exempt
@require_POST
def jira_webhook(request: HttpRequest, token: str) -> HttpResponse:
    """Jira says a task changed (ADR 0020). Jira 8.13 cannot sign its webhooks, so the secret
    token in the address is the only proof the call comes from our Jira; the body is never
    read. The answer is instant: Jira 8.13 waits for it before it finishes saving the task."""
    expected = settings.TRACKER_JIRA_WEBHOOK_TOKEN
    if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
        raise Http404
    key = request.GET.get("key", "")
    if not services.TASK_KEY.fullmatch(key):
        return HttpResponseBadRequest("The webhook address in Jira needs ?key=${issue.key}.")
    services.wake_for_webhook(key)
    return HttpResponse(status=204)
