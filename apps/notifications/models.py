from django.db import models

from apps.accounts.models import User


class EmailKind(models.TextChoices):
    SIGN_IN_LINK = "sign_in_link", "Sign-in link"
    INVITE = "invite", "Invite"


class EmailStatus(models.TextChoices):
    SENT = "sent", "Sent"
    FAILED = "failed", "Not sent"


class Email(models.Model):
    """One attempt to send one email: the journal behind Logs → Emails (PRD: notifications).

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
    error = models.TextField(blank=True, help_text="Why the mail server did not take it.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} to {self.to}"
