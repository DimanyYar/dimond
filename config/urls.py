from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path

from apps.core.views import health, home, static_file, styleguide

urlpatterns = [
    path("", home, name="home"),
    path("", include("apps.accounts.urls")),
    path("", include("apps.requests.urls")),
    path("healthz", health, name="health"),
    path("styleguide/", styleguide, name="styleguide"),
    path(settings.ADMIN_URL, admin.site.urls),
]

if settings.DEBUG:
    # Replaces runserver's own static handler, which docker-compose turns off with --nostatic.
    urlpatterns.append(re_path(r"^static/(?P<path>.*)$", static_file))
