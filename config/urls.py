from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.core.views import health, home, styleguide

urlpatterns = [
    path("", home, name="home"),
    path("", include("apps.accounts.urls")),
    path("healthz", health, name="health"),
    path("styleguide/", styleguide, name="styleguide"),
    path(settings.ADMIN_URL, admin.site.urls),
]
