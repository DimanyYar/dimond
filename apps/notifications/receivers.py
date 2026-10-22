from typing import Any

from anymail.signals import AnymailTrackingEvent, EventType, tracking
from django.dispatch import receiver
from django.utils import timezone

from .models import EmailStatus
from .services import record_delivery_report

# Resend's reports dimond keeps (ADR 0021); "sent", "delayed", opens and clicks are not.
REPORTS = {
    EventType.DELIVERED: EmailStatus.DELIVERED,
    EventType.BOUNCED: EmailStatus.BOUNCED,
    EventType.COMPLAINED: EmailStatus.SPAM,
}


@receiver(tracking)
def keep_delivery_report(sender: Any, event: AnymailTrackingEvent, **kwargs: Any) -> None:
    status = REPORTS.get(event.event_type)
    if status is None or not event.message_id:
        return
    record_delivery_report(
        event.message_id, status, event.timestamp or timezone.now(), event.description or ""
    )
