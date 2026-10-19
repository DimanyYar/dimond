from django.conf import settings

from .base import Tracker, TrackerNotConfigured
from .jira import JiraTracker
from .mock import MockTracker
from .models import TrackerKind, TrackerSettings


def jira_is_configured() -> bool:
    return bool(
        settings.TRACKER_JIRA_URL
        and settings.TRACKER_JIRA_USERNAME
        and settings.TRACKER_JIRA_PASSWORD
    )


def current_tracker() -> Tracker:
    """The tracker chosen in the admin, ready to use."""
    if TrackerSettings.load().tracker == TrackerKind.JIRA:
        if not jira_is_configured():
            raise TrackerNotConfigured(
                "Jira is chosen in the admin, but TRACKER_JIRA_URL, TRACKER_JIRA_USERNAME or "
                "TRACKER_JIRA_PASSWORD is empty in .env."
            )
        return JiraTracker(
            settings.TRACKER_JIRA_URL,
            settings.TRACKER_JIRA_USERNAME,
            settings.TRACKER_JIRA_PASSWORD,
        )
    return MockTracker()
