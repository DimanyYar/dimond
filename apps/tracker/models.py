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
