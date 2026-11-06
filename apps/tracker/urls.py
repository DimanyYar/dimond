from django.urls import path

from . import views

urlpatterns = [
    path("releases/", views.releases, name="releases"),
    path("releases/<int:pk>/visibility/", views.release_visibility, name="release_visibility"),
    path("releases/tasks/<str:key>/move/", views.move_task, name="move_task"),
    path("releases/moves/<int:pk>/undo/", views.undo_move, name="undo_move"),
    path("logs/", views.logs, name="logs"),
    path("logs/releases/", views.logs_releases, name="logs_releases"),
    path("logs/sync/", views.sync_now, name="sync_now"),
    path("jira/webhook/<str:token>/", views.jira_webhook, name="jira_webhook"),
]
