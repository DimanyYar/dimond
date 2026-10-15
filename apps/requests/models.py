from django.db import models

from apps.accounts.models import Company, User


class RequestType(models.TextChoices):
    """The four kinds of request a client can send (PRD: request types and fields)."""

    BUG = "bug", "Bug"
    IMPROVEMENT = "improvement", "Improvement"
    FEATURE = "feature", "New feature"
    IDEA = "idea", "Idea"


class ClientStatus(models.TextChoices):
    """What the client sees instead of the Jira workflow (PRD: statuses and Jira mapping)."""

    RECEIVED = "received", "Received"
    NEEDS_INPUT = "needs_input", "Needs your input"
    PLANNED = "planned", "Planned"
    IN_PROGRESS = "in_progress", "In progress"
    READY_TO_CHECK = "ready_to_check", "Ready to check"
    DONE = "done", "Done"
    DECLINED = "declined", "Declined"
    LATER = "later", "Later"


class Request(models.Model):
    """Something a client asked for. Everyone at the client's company can see it."""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="requests")
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="requests")
    type = models.CharField(max_length=20, choices=RequestType.choices)
    title = models.CharField(max_length=200)
    why = models.TextField(help_text="Why it matters: the context behind the request.")
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=ClientStatus.choices, default=ClientStatus.RECEIVED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class StatusEvent(models.Model):
    """One status change. Kept forever: period summaries and PM reports are built from it."""

    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="status_events")
    from_status = models.CharField(
        max_length=20, choices=ClientStatus.choices, blank=True, help_text="Empty when created."
    )
    to_status = models.CharField(max_length=20, choices=ClientStatus.choices)
    actor = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Empty when the Jira sync changed the status.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"{self.from_status or 'new'} → {self.to_status}"
