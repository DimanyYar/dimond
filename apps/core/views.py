from django.conf import settings
from django.db import connection
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET


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
