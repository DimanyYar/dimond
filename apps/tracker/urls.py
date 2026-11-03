from django.urls import path

from . import views

urlpatterns = [
    path("releases/", views.releases, name="releases"),
    path("releases/<int:pk>/visibility/", views.release_visibility, name="release_visibility"),
    path("logs/", views.logs, name="logs"),
    path("logs/sync/", views.sync_now, name="sync_now"),
    path("jira/webhook/<str:token>/", views.jira_webhook, name="jira_webhook"),
]
