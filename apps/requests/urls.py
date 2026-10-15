from django.urls import path

from . import views

urlpatterns = [
    path("requests/new/", views.new_request, name="new_request"),
]
