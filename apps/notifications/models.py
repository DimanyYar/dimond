from django.db import models
from django.utils import timezone

from apps.accounts.models import User


class EmailKind(models.TextChoices):
    SIGN_IN_LINK = "sign_in_link", "Sign-in link"
    INVITE = "invite", "Invite"
    REQUEST_UPDATE = "request_update", "Request update"
    PM_UPDATE = "pm_update", "To the PM"


class EmailStatus(models.TextChoices):
    SENT = "sent", "Sent"
    FAILED = "failed", "Not sent"
    # What Resend reported afterwards (ADR 0021).
    DELIVERED = "delivered", "Delivered"
    BOUNCED = "bounced", "Bounced"
    SPAM = "spam", "Marked spam"


class Email(models.Model):
    """One email and every try to send it: the journal behind Logs → Emails (PRD:
    notifications). Tries of the same email update its row instead of adding rows.

    Only what the PM needs to answer "did it go out?" is kept: never the body, so a sign-in
    link or a message never lands here.
    """

    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Empty once the person is deleted, e.g. a cancelled invite.",
    )
    to = models.EmailField(help_text="The address as it was when the email went out.")
    kind = models.CharField(max_length=20, choices=EmailKind.choices)
    subject = models.CharField(max_length=300)
    status = models.CharField(max_length=10, choices=EmailStatus.choices)
    provider_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="The id Resend gave the email; empty for Mailpit, which gives none.",
    )
    error = models.CharField(
        max_length=100, blank=True, help_text="Why it did not go out, in plain words."
    )
    error_detail = models.TextField(blank=True, help_text="The error as the mail server put it.")
    attempts = models.PositiveIntegerField(default=1, help_text="Tries so far, this one included.")
    created_at = models.DateTimeField(auto_now_add=True, help_text="The first try.")
    tried_at = models.DateTimeField(default=timezone.now, help_text="The latest try.")
    delivered_at = models.DateTimeField(
        null=True, blank=True, help_text="When Resend reported it reached the inbox."
    )

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} to {self.to}"

    @property
    def delivery_seconds(self) -> float | None:
        """From the last try to Resend's "delivered" report, as Logs shows it."""
        if self.delivered_at is None:
            return None
        return (self.delivered_at - self.tried_at).total_seconds()
