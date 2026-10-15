from typing import NamedTuple

from django.db import models

from apps.accounts.models import Company, User


class RequestType(models.TextChoices):
    """The four kinds of request a client can send (PRD: request types and fields)."""

    BUG = "bug", "Bug"
    IMPROVEMENT = "improvement", "Improvement"
    FEATURE = "feature", "New feature"
    IDEA = "idea", "Idea"


class WhenNeeded(models.TextChoices):
    """How urgent the client says it is. The PM decides the actual release ("Planned for").

    The form starts at Not sure for every type: the client raises the urgency on purpose,
    instead of keeping a guess we made for them.
    """

    HOTFIX = "hotfix", "Hotfix"
    THIS_RELEASE = "this_release", "This release"
    NEXT_RELEASE = "next_release", "Next release"
    NICE_TO_HAVE = "nice_to_have", "Nice to have"
    NOT_SURE = "not_sure", "Not sure"


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


CLOSED_STATUSES = (ClientStatus.DONE, ClientStatus.DECLINED)
IN_WORK_STATUSES = (
    ClientStatus.NEEDS_INPUT,
    ClientStatus.PLANNED,
    ClientStatus.IN_PROGRESS,
    ClientStatus.READY_TO_CHECK,
)


class BoardColumn(NamedTuple):
    slug: str
    name: str
    statuses: tuple[ClientStatus, ...]


# Fewer columns than statuses: the client cares whether a request waits for them, waits in
# line, is being built, needs checking, was parked or is over.
BOARD_COLUMNS = (
    BoardColumn("needs-you", "Needs you", (ClientStatus.NEEDS_INPUT,)),
    BoardColumn("queued", "Queued", (ClientStatus.RECEIVED, ClientStatus.PLANNED)),
    BoardColumn("in-progress", "In progress", (ClientStatus.IN_PROGRESS,)),
    BoardColumn("ready-to-check", "Ready to check", (ClientStatus.READY_TO_CHECK,)),
    BoardColumn("later", "Later", (ClientStatus.LATER,)),
    BoardColumn("done", "Done", CLOSED_STATUSES),
)


class Request(models.Model):
    """Something a client asked for. Everyone at the client's company can see it."""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="requests")
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="requests")
    type = models.CharField(max_length=20, choices=RequestType.choices)
    title = models.CharField(max_length=200)
    why = models.TextField(help_text="Why it matters: the context behind the request.")
    description = models.TextField(blank=True)
    steps = models.TextField(blank=True, help_text="Steps to reproduce; bugs only.")
    link = models.URLField(max_length=500, blank=True, help_text="Required for bugs.")
    when_needed = models.CharField(max_length=20, choices=WhenNeeded.choices)
    status = models.CharField(
        max_length=20, choices=ClientStatus.choices, default=ClientStatus.RECEIVED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title

    @property
    def key(self) -> str:
        """The number clients and the PM use for a request, like a Jira key."""
        return f"DMN-{self.pk}"


class StatusEvent(models.Model):
    """One status change. Kept forever: period summaries and PM reports are built from it."""

    request = models.ForeignKey(Request, on_delete=models.PROTECT, related_name="status_events")
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
