from django.urls import path

from . import views

urlpatterns = [
    path("logs/emails/", views.logs_emails, name="logs_emails"),
]
