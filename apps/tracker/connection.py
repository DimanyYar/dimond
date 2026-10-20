import hashlib
import hmac

from django.conf import settings

from .base import Tracker, TrackerNotConfigured
from .jira import JiraTracker
from .mock import MockTracker
from .models import TrackerKind, TrackerSettings


def jira_is_configured() -> bool:
    """The admin and the adapter agree on this: an http address is as good as none, because
    the adapter refuses to send the password over it."""
    return bool(
        settings.TRACKER_JIRA_URL.startswith("https://")
        and settings.TRACKER_JIRA_USERNAME
        and settings.TRACKER_JIRA_PASSWORD
    )


def current_tracker() -> Tracker:
    """The tracker chosen in the admin, ready to use."""
    if TrackerSettings.load().tracker == TrackerKind.JIRA:
        if not jira_is_configured():
            raise TrackerNotConfigured(
                "Jira is chosen in the admin, but TRACKER_JIRA_URL, TRACKER_JIRA_USERNAME or "
                "TRACKER_JIRA_PASSWORD is empty in .env, or the address does not start with https://."
            )
        return JiraTracker(
            settings.TRACKER_JIRA_URL,
            settings.TRACKER_JIRA_USERNAME,
            settings.TRACKER_JIRA_PASSWORD,
        )
    return MockTracker()


def login_fingerprint() -> str:
    """The Jira login in .env as a keyed hash, never the password itself: it tells whether the
    login Jira rejected is still the one in use."""
    login = "\n".join(
        [settings.TRACKER_JIRA_URL, settings.TRACKER_JIRA_USERNAME, settings.TRACKER_JIRA_PASSWORD]
    )
    return hmac.new(settings.SECRET_KEY.encode(), login.encode(), hashlib.sha256).hexdigest()


def jira_login_paused() -> bool:
    """Jira rejected the login that is still in .env: anything that would sign in to Jira
    waits, since each try brings Jira closer to locking the account. Only reads; the sync
    worker is what lifts the pause."""
    row = TrackerSettings.load()
    return (
        row.sync_paused_at is not None
        and row.tracker == TrackerKind.JIRA
        and row.sync_paused_login == login_fingerprint()
    )
