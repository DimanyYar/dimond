from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles.views import serve
from django.db import connection
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.http.response import HttpResponseBase
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_GET, require_safe

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
    """Where a signed-in person lands: clients and the PM go to the requests page; the team,
    who only open attachment links, get a short note."""
    assert isinstance(request.user, User)  # login_required lets only signed-in people in
    if has_my_requests_page(request.user):
        return redirect("my_requests")
    return render(request, "core/home.html")


@require_safe
def static_file(request: HttpRequest, path: str) -> HttpResponseBase:
    """Serves CSS and scripts in development. Without a Cache-Control header browsers keep an
    old stylesheet for a while, so a CSS change needed a hard reload. "no-cache" makes the
    browser ask every time; an unchanged file still comes back as a short 304."""
    response = serve(request, path)
    patch_cache_control(response, no_cache=True)
    return response


@require_safe
def favicon(request: HttpRequest) -> HttpResponse:
    """Browsers look for /favicon.ico on pages that do not name their icon, such as an image
    opened from a request; send them to dimond's icon instead of a 404."""
    return redirect(static("icons/favicon.ico"), permanent=True)
