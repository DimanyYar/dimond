from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
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
