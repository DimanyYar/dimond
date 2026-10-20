"""When Jira rejects the login the sync pauses, so Jira does not lock the account behind a
captcha; when Jira does not answer it tries once more two minutes later (PRD: Jira sync)."""

import pytest
from django.urls import reverse

from apps.tracker.base import Issue, TrackerSignInFailed, TrackerUnreachable
from apps.tracker.models import SyncOutcome, SyncRun, TrackerKind, TrackerSettings
from apps.tracker.services import RETRY_SECONDS, seconds_until_next, sync_paused, sync_tasks

pytestmark = pytest.mark.django_db

EVERY = 900


class Jira:
    def __init__(self, fails: Exception | None = None) -> None:
        self.fails = fails
        self.asked = 0

    def issues(self, keys: list[str]) -> list[Issue]:
        self.asked += 1
        if self.fails:
            raise self.fails
        return []


@pytest.fixture
def jira(monkeypatch, settings) -> Jira:
    settings.TRACKER_JIRA_URL = "https://j.shakuro.com"
    settings.TRACKER_JIRA_USERNAME = "dimond-sync"
    settings.TRACKER_JIRA_PASSWORD = "old-password"
    TrackerSettings.objects.update_or_create(pk=1, defaults={"tracker": TrackerKind.JIRA})
    fake = Jira(fails=TrackerSignInFailed("Jira did not accept the login 'dimond-sync'."))
    monkeypatch.setattr("apps.tracker.services.current_tracker", lambda: fake)
    return fake


def test_a_rejected_login_pauses_the_sync_at_once(jira):
    run = sync_tasks()

    assert run.outcome == SyncOutcome.FAILED
    assert sync_paused()
    assert (
        TrackerSettings.load().sync_paused_reason == "Jira did not accept the login 'dimond-sync'."
    )


def test_the_password_itself_is_never_stored(jira):
    sync_tasks()

    row = TrackerSettings.load()
    assert "old-password" not in row.sync_paused_login
    assert len(row.sync_paused_login) == 64


def test_the_sync_resumes_by_itself_once_the_login_in_env_changes(jira, settings):
    sync_tasks()

    settings.TRACKER_JIRA_PASSWORD = "new-password"

    assert not sync_paused()
    assert TrackerSettings.load().sync_paused_at is None


def test_choosing_the_demo_tracker_lifts_the_pause(jira):
    sync_tasks()

    TrackerSettings.objects.filter(pk=1).update(tracker=TrackerKind.DEMO)

    assert not sync_paused()


def test_a_run_that_reaches_jira_lifts_the_pause(jira):
    sync_tasks()
    jira.fails = None

    sync_tasks(SyncRun.objects.create(outcome=SyncOutcome.QUEUED, manual=True))

    assert TrackerSettings.load().sync_paused_at is None


def test_no_answer_from_jira_is_retried_once_two_minutes_later(jira):
    jira.fails = TrackerUnreachable("Jira at https://j.shakuro.com did not answer: timed out")

    first = sync_tasks()
    assert seconds_until_next(first, EVERY, retried=False) == (RETRY_SECONDS, True)

    second = sync_tasks()
    assert second.error == "Jira at https://j.shakuro.com did not answer: timed out"
    assert seconds_until_next(second, EVERY, retried=True) == (EVERY, False)


def test_a_rejected_login_is_not_retried(jira):
    run = sync_tasks()

    assert seconds_until_next(run, EVERY, retried=False) == (EVERY, False)


def test_a_good_run_waits_the_usual_interval(jira):
    jira.fails = None

    assert seconds_until_next(sync_tasks(), EVERY, retried=False) == (EVERY, False)


def test_the_pm_sees_the_pause_on_logs_and_in_the_sidebar(jira, client, pm):
    sync_tasks()
    client.force_login(pm)

    page = client.get(reverse("logs")).content.decode()

    assert "Jira sync is paused." in page
    assert "Jira did not accept the login" in page
    assert "Jira sync paused: login rejected" in page
