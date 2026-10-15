from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from apps.accounts.models import User
from apps.requests.selectors import has_my_requests_page


@require_GET
def health(request: HttpRequest) -> JsonResponse:
    """Liveness check for Docker and the reverse proxy: the app is up and the database answers."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


@require_GET
def styleguide(request: HttpRequest) -> HttpResponse:
    """Every UI-kit component in real CSS, to compare with the mockups. Development only."""
    if not settings.DEBUG:
        raise Http404
    return render(request, "core/styleguide.html")


@require_GET
@login_required
def home(request: HttpRequest) -> HttpResponse:
    """Where a signed-in person lands: clients go to their requests, the PM gets the inbox
    once it exists."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    if has_my_requests_page(request.user):
        return redirect("my_requests")
    return render(request, "core/home.html")
