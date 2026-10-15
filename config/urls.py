from django.conf import settings
from django.contrib import admin
from django.urls import path

from apps.core.views import health, styleguide

urlpatterns = [
    path("healthz", health, name="health"),
    path("styleguide/", styleguide, name="styleguide"),
    path(settings.ADMIN_URL, admin.site.urls),
]
