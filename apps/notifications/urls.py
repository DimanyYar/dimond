from django.urls import path

from . import views

urlpatterns = [
    path("logs/emails/", views.logs_emails, name="logs_emails"),
    path("logs/emails/try/", views.try_emails_now, name="try_emails_now"),
    path("logs/discord/", views.logs_discord, name="logs_discord"),
    path("resend/webhook/", views.resend_webhook, name="resend_webhook"),
]
