from django.db import models


class TrackerKind(models.TextChoices):
    JIRA = "jira", "Jira"
    DEMO = "demo", "Demo (built-in tasks, no real tracker)"


class TrackerSettings(models.Model):
    """The one row of tracker settings, switched in the admin (PRD: tracker and settings).

    The tracker's address and password never live here: they come from .env, so neither the
    admin nor a database backup ever shows them.
    """

    tracker = models.CharField(
        max_length=20,
        choices=TrackerKind.choices,
        default=TrackerKind.DEMO,
        help_text="Where dimond reads and creates tasks. Jira needs TRACKER_JIRA_URL, "
        "TRACKER_JIRA_USERNAME and TRACKER_JIRA_PASSWORD in .env.",
    )
    sync_paused_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Jira rejected the login and the sync stopped trying, so Jira does not lock "
        "the account behind a captcha. It resumes once the login in .env changes.",
    )
    sync_paused_reason = models.TextField(blank=True, help_text="What Jira said.")
    sync_paused_captcha = models.BooleanField(
        default=False,
        help_text="Jira wants a captcha: the password may be right, a person signs in to Jira "
        "in a browser once, then Sync now tries again.",
    )
    sync_paused_login = models.CharField(
        max_length=64,
        blank=True,
        help_text="A fingerprint of the Jira login in .env when the sync paused, never the "
        "password itself: a different one means someone fixed it.",
    )
    clients_see_jira_links = models.BooleanField(
        default=False,
        help_text="Clients always see the task key on their request. On: the key opens the task "
        "in Jira (for clients who have access to our Jira). Off: just the key.",
    )

    class Meta:
        verbose_name = verbose_name_plural = "tracker settings"
        constraints = [models.CheckConstraint(condition=models.Q(pk=1), name="one_row_only")]

    def __str__(self) -> str:
        return "Tracker settings"

    def save(self, *args: object, **kwargs: object) -> None:
        self.pk = 1
        super().save(*args, **kwargs)  # type: ignore[arg-type]

    @classmethod
    def load(cls) -> "TrackerSettings":
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings


class SyncOutcome(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    PROBLEMS = "problems", "Finished with problems"
    FAILED = "failed", "Failed"


class SyncRun(models.Model):
    """One pass of the Jira sync, kept for the sync log (PRD: Jira sync)."""

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(
        max_length=20, choices=SyncOutcome.choices, default=SyncOutcome.RUNNING
    )
    manual = models.BooleanField(default=False, help_text="Asked for with Sync now.")
    webhook_keys = models.JSONField(
        default=list,
        blank=True,
        help_text="Tasks a Jira webhook said changed while this run waited, in arrival order. "
        "The run still reads every linked task: the webhook only made it start sooner.",
    )
    checked = models.PositiveIntegerField(default=0, help_text="Linked tasks read from Jira.")
    error = models.TextField(blank=True, help_text="Why the run failed, as the tracker said.")
    jira_silent = models.BooleanField(
        default=False,
        help_text="Failed because Jira did not answer (a timeout or a server error): worth one "
        "more try soon, unlike a refusal.",
    )
    releases_copied = models.PositiveIntegerField(
        default=0, help_text="Tasks and subtasks of releases copied from Jira (ADR 0026)."
    )
    releases_error = models.TextField(
        blank=True,
        help_text="Why copying the releases failed; the status sync went on regardless.",
    )

    class Meta:
        ordering = ["-started_at", "-pk"]

    def __str__(self) -> str:
        return f"Sync {self.started_at:%Y-%m-%d %H:%M} · {self.get_outcome_display()}"


class SyncEvent(models.Model):
    """Something a sync run noticed about one linked task."""

    run = models.ForeignKey(SyncRun, on_delete=models.CASCADE, related_name="events")
    request = models.ForeignKey(
        "requests.Request", on_delete=models.CASCADE, related_name="sync_events"
    )
    task_key = models.CharField(max_length=32)
    what = models.CharField(max_length=300, help_text="What changed in Jira.")
    effect = models.CharField(max_length=300, help_text="What it meant for the client.")
    needs_attention = models.BooleanField(
        default=False, help_text="A problem the PM should look at, e.g. an unmapped status."
    )

    class Meta:
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"{self.task_key}: {self.what}"


class TrackerVersion(models.Model):
    """A copy of a fix version of a client's Jira project, for the Releases page (#30; ADR
    0026). The sync rewrites it on every run; Jira stays the source of truth."""

    project_key = models.CharField(max_length=32)
    jira_id = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    start_date = models.DateField(null=True, blank=True)
    release_date = models.DateField(null=True, blank=True)
    released = models.BooleanField(default=False)
    # The project's own order of versions in Jira, which "the previous release" follows.
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["project_key", "position"]

    def __str__(self) -> str:
        return f"{self.project_key} {self.name}"


class TrackerSprint(models.Model):
    jira_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255)
    state = models.CharField(max_length=20)
    starts = models.DateTimeField(null=True, blank=True)
    ends = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.name


class TrackerTask(models.Model):
    """A copy of a task or subtask of a release, linked to a request or not (ADR 0026)."""

    project_key = models.CharField(max_length=32)
    key = models.CharField(max_length=32, unique=True)
    summary = models.CharField(max_length=255)
    issue_type = models.CharField(max_length=64)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="subtasks"
    )
    status = models.CharField(max_length=64)
    status_category = models.CharField(max_length=20)
    assignee_login = models.CharField(max_length=255, blank=True)
    assignee_name = models.CharField(max_length=255, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    versions = models.ManyToManyField(TrackerVersion, blank=True, related_name="tasks")
    sprints = models.ManyToManyField(TrackerSprint, blank=True, related_name="tasks")

    class Meta:
        ordering = ["key"]

    def __str__(self) -> str:
        return self.key
