from django.urls import path

from . import views

urlpatterns = [
    path("files/<uuid:attachment_id>/", views.open_file, name="open_file"),
    path("files/<uuid:attachment_id>/preview/", views.open_preview, name="open_preview"),
]
