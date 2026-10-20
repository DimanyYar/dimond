from django.urls import path

from . import views

urlpatterns = [
    path("logs/", views.logs, name="logs"),
    path("logs/sync/", views.sync_now, name="sync_now"),
]
