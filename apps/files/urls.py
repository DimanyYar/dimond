from django.urls import path

from . import views

urlpatterns = [
    path("files/<uuid:attachment_id>/", views.open_file, name="open_file"),
    path("files/<uuid:attachment_id>/preview/", views.open_preview, name="open_preview"),
    path("files/<uuid:attachment_id>/share/", views.share_file_again, name="share_file_again"),
    path("p/<str:token>/", views.open_public_file, name="public_file"),
]
