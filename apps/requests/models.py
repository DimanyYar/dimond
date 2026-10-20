from typing import NamedTuple

from django.db import models
from django.urls import reverse

from apps.accounts.models import Company, User


class RequestType(models.TextChoices):
    """The four kinds of request a client can send (PRD: request types and fields)."""

    BUG = "bug", "Bug"
    IMPROVEMENT = "improvement", "Improvement"
    FEATURE = "feature", "New feature"
    IDEA = "idea", "Idea"
    # Only a voice request can leave the type open; the PM picks one when sorting it.
    NOT_SURE = "not_sure", "Not sure"


# The types the request form and the filters offer: a client filling in the form picks one.
FORM_TYPES = [
    (value, label) for value, label in RequestType.choices if value != RequestType.NOT_SURE
]


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
    WITHDRAWN = "withdrawn", "Withdrawn"


CLOSED_STATUSES = (ClientStatus.DONE, ClientStatus.DECLINED, ClientStatus.WITHDRAWN)
# Until a request is planned it has no Jira task yet, so the client may still rewrite it
# or withdraw it on the spot (PRD: editing a request).
EDITABLE_STATUSES = (ClientStatus.RECEIVED, ClientStatus.NEEDS_INPUT)
# Once the PM has taken it on, the client asks to withdraw and the PM decides: the team may
# already be working on it.
ASK_TO_WITHDRAW_STATUSES = (
    ClientStatus.PLANNED,
    ClientStatus.IN_PROGRESS,
    ClientStatus.READY_TO_CHECK,
    ClientStatus.LATER,
)
IN_WORK_STATUSES = (
    ClientStatus.NEEDS_INPUT,
    ClientStatus.PLANNED,
    ClientStatus.IN_PROGRESS,
    ClientStatus.READY_TO_CHECK,
)
# What the client sees for each status of the Jira task (PRD: statuses and Jira mapping). One
# table for every project: Shakuro's workflows share these names. Any other status leaves the
# client's status as it is and shows up in the sync log.
JIRA_STATUSES = {
    "To Do": ClientStatus.PLANNED,
    "In Progress": ClientStatus.IN_PROGRESS,
    "Code review": ClientStatus.IN_PROGRESS,
    "Test Ready": ClientStatus.IN_PROGRESS,
    "Staging Ready": ClientStatus.IN_PROGRESS,
    "on Staging": ClientStatus.READY_TO_CHECK,
    "Production Ready": ClientStatus.READY_TO_CHECK,
    "on Production": ClientStatus.DONE,
    "Done": ClientStatus.DONE,
}
# The statuses the sync sets, in the order work goes. The client's status only moves forward
# along it: a task sent back from staging to development is noise to the client.
TASK_PROGRESS = (
    ClientStatus.PLANNED,
    ClientStatus.IN_PROGRESS,
    ClientStatus.READY_TO_CHECK,
    ClientStatus.DONE,
)


class BoardColumn(NamedTuple):
    slug: str
    name: str
    statuses: tuple[ClientStatus, ...]

    @property
    def mixes_statuses(self) -> bool:
        """Cards in a column that holds several statuses say which one they are in."""
        return len(self.statuses) > 1


# Fewer columns than statuses: the client cares whether a request waits for them, waits in
# line, is being built, needs checking, was parked or is over. A planned request already has
# a Jira task, so the team has taken it on: it sits with the work in progress.
NEEDS_YOU_COLUMN = "needs-you"
QUEUED_COLUMN = "queued"
BOARD_COLUMNS = (
    BoardColumn(NEEDS_YOU_COLUMN, "Needs you", (ClientStatus.NEEDS_INPUT,)),
    BoardColumn(QUEUED_COLUMN, "Queued", (ClientStatus.RECEIVED,)),
    BoardColumn("in-progress", "In progress", (ClientStatus.PLANNED, ClientStatus.IN_PROGRESS)),
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
    why = models.TextField(
        blank=True,
        help_text="Why it matters: the context behind the request. Empty for a voice request, "
        "where the client says it.",
    )
    description = models.TextField(blank=True)
    steps = models.TextField(blank=True, help_text="Steps to reproduce; bugs only.")
    link = models.URLField(max_length=500, blank=True, help_text="Required for bugs.")
    when_needed = models.CharField(max_length=20, choices=WhenNeeded.choices)
    from_voice = models.BooleanField(
        default=False,
        help_text="Sent with “Just say it”: the recording is the request, the text a draft.",
    )
    status = models.CharField(
        max_length=20, choices=ClientStatus.choices, default=ClientStatus.RECEIVED
    )
    jira_key = models.CharField(
        max_length=32, blank=True, help_text="The Jira task made from this request, e.g. FI-2301."
    )
    jira_url = models.URLField(max_length=500, blank=True)
    jira_status = models.CharField(
        max_length=100,
        blank=True,
        help_text="The task's status in Jira as the last sync saw it, e.g. Code review.",
    )
    lost_jira_key = models.CharField(
        max_length=32,
        blank=True,
        help_text="The task this request followed until it was deleted in Jira; the sync "
        "unlinked it. Cleared when the PM links another task.",
    )
    jira_moved_to = models.CharField(
        max_length=32,
        blank=True,
        help_text="Jira moved the task into another client's project under this key; the sync "
        "stopped following it until the PM links another task or unlinks it.",
    )
    pm_handled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The PM has dealt with everything written up to this moment (No reply "
        "needed, Later). A newer client message puts the request back into Needs you.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One task, one request (PRD: Existing task): a second click or a second PM cannot
            # tie the same task to another request.
            models.UniqueConstraint(
                fields=["jira_key"], condition=~models.Q(jira_key=""), name="one_request_per_task"
            )
        ]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        # Also gives the admin its "View on site" button: the PM's way to the card until the
        # inbox (#12) exists.
        return reverse("request_detail", args=[self.pk])

    @property
    def key(self) -> str:
        """The number clients and the PM use for a request, like a Jira key."""
        return f"DMN-{self.pk}"


# What the client fills in the form; an edit may change any of them.
REQUEST_FIELDS = ("type", "title", "description", "steps", "why", "when_needed", "link")


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


class Message(models.Model):
    """A reply in a request's thread. Everyone who can open the request reads every message,
    so PM notes and Jira comments never go here."""

    request = models.ForeignKey(Request, on_delete=models.PROTECT, related_name="messages")
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    text = models.TextField()
    asks_for_input = models.BooleanField(
        default=False, help_text="The PM's question that moved the request to Needs your input."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"{self.author} on {self.request.key}"


class RequestRevision(models.Model):
    """The request as it was before one edit. The card lists them under "edited", so the PM
    and the client's colleagues see what changed and who changed it."""

    request = models.ForeignKey(Request, on_delete=models.PROTECT, related_name="revisions")
    editor = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="+", help_text="Who made the edit."
    )
    type = models.CharField(max_length=20, choices=RequestType.choices)
    title = models.CharField(max_length=200)
    why = models.TextField(blank=True)
    description = models.TextField(blank=True)
    steps = models.TextField(blank=True)
    link = models.URLField(max_length=500, blank=True)
    when_needed = models.CharField(max_length=20, choices=WhenNeeded.choices)
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the edit was made.")

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"{self.request.key} before the edit of {self.created_at:%Y-%m-%d %H:%M}"


class WithdrawalRequest(models.Model):
    """A client's ask to withdraw a request the PM has already taken on. Waits until the PM
    confirms it (the request becomes Withdrawn) or keeps the request going."""

    request = models.ForeignKey(
        Request, on_delete=models.PROTECT, related_name="withdrawal_requests"
    )
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    # The reason is a client message in the thread, so everyone reads it where they read
    # everything else; the ask points at it for the PM's banner.
    reason = models.OneToOneField(Message, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed = models.BooleanField(
        null=True, blank=True, help_text="Empty while the PM has not decided."
    )
    decided_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            # One open ask per request: asking twice would leave the PM two buttons for one
            # decision.
            models.UniqueConstraint(
                fields=["request"],
                condition=models.Q(confirmed__isnull=True),
                name="one_pending_withdrawal_per_request",
            )
        ]

    def __str__(self) -> str:
        return f"Withdraw {self.request.key}?"


class RequestVisit(models.Model):
    """When a person last opened a request. Messages from others after that moment are new
    to them: the board shows "2 new" and the thread a "new since" line (PRD: unread)."""

    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="visits")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    seen_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["request", "user"], name="one_visit_per_person")
        ]

    def __str__(self) -> str:
        return f"{self.user} saw {self.request.key} at {self.seen_at:%Y-%m-%d %H:%M}"


class InternalNote(models.Model):
    """The PM's note on a request. Its own table, never a flag on Message: client pages only
    ever read messages, so a forgotten filter cannot show a note to a client (PRD: internal
    note). Notes stay in dimond and are never sent to Jira either."""

    request = models.ForeignKey(Request, on_delete=models.PROTECT, related_name="notes")
    author = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Empty when dimond wrote it, e.g. the sync telling the PM a task is gone.",
    )
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"Note by {self.author} on {self.request.key}"
