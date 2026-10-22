from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "apps.notifications"

    def ready(self) -> None:
        # Connects Resend's delivery reports to the journal (ADR 0021).
        from . import receivers  # noqa: F401
