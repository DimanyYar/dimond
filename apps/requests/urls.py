from django.urls import path

from . import views

urlpatterns = [
    path("requests/", views.my_requests, name="my_requests"),
    path("requests/count/", views.my_requests_count, name="my_requests_count"),
    path("requests/new/", views.new_request, name="new_request"),
    path("requests/new/voice/", views.new_voice_request, name="new_voice_request"),
    path("requests/<int:pk>/", views.request_detail, name="request_detail"),
    path("requests/<int:pk>/edit/", views.request_edit, name="request_edit"),
    path("requests/<int:pk>/withdraw/", views.request_withdraw, name="request_withdraw"),
    path(
        "requests/<int:pk>/withdrawal/",
        views.request_withdrawal_decision,
        name="request_withdrawal_decision",
    ),
    path("requests/<int:pk>/triage/", views.request_triage, name="request_triage"),
    path("requests/<int:pk>/decline/", views.request_decline, name="request_decline"),
]
