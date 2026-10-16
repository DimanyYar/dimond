from django.urls import path

from . import views

urlpatterns = [
    path("requests/", views.my_requests, name="my_requests"),
    path("requests/count/", views.my_requests_count, name="my_requests_count"),
    path("requests/new/", views.new_request, name="new_request"),
    path("requests/<int:pk>/", views.request_detail, name="request_detail"),
]
