from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path

from apps.core.views import favicon, health, home, static_file, styleguide

urlpatterns = [
    path("", home, name="home"),
    path("", include("apps.accounts.urls")),
    path("", include("apps.requests.urls")),
    path("", include("apps.files.urls")),
    path("healthz", health, name="health"),
    path("favicon.ico", favicon),
    path("styleguide/", styleguide, name="styleguide"),
    path(settings.ADMIN_URL, admin.site.urls),
]

handler404 = "apps.core.views.not_found"

if settings.DEBUG:
    # Replaces runserver's own static handler, which docker-compose turns off with --nostatic.
    urlpatterns.append(re_path(r"^static/(?P<path>.*)$", static_file))
