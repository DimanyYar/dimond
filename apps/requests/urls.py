from django.urls import path

from . import views

urlpatterns = [
    path("requests/", views.my_requests, name="my_requests"),
    path("requests/new/", views.new_request, name="new_request"),
]
