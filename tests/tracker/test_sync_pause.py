"""When Jira rejects the login the sync pauses, so Jira does not lock the account behind a
captcha; when Jira does not answer it tries once more two minutes later (PRD: Jira sync)."""

import pytest
from django.urls import reverse

from apps.tracker.base import (
    Issue,
    TrackerCaptcha,
    TrackerRefused,
    TrackerSignInFailed,
    TrackerUnreachable,
)
from apps.tracker.models import SyncOutcome, SyncRun, TrackerKind, TrackerSettings
from apps.tracker.services import (
    RETRY_SECONDS,
    STILL_PAUSED,
    seconds_until_next,
    sync_paused,
    sync_tasks,
)

pytestmark = pytest.mark.django_db

EVERY = 900


class Jira:
    """Counts what reaches "Jira": `issues` with keys, and `account` (who dimond signs in as)."""

    def __init__(self, fails: Exception | None = None) -> None:
        self.fails = fails
        self.asked = 0

    def account(self) -> None:
        self._ask()

    def issues(self, keys: list[str]) -> list[Issue]:
        if keys:
            self._ask()
        return []

    def issue(self, key: str) -> Issue:
        self._ask()
        raise AssertionError("not reached in these tests")

    def _ask(self) -> None:
        self.asked += 1
        if self.fails:
            raise self.fails


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


def sync_now() -> SyncRun:
    return sync_tasks(SyncRun.objects.create(outcome=SyncOutcome.QUEUED, manual=True))


def test_sync_now_does_not_retry_a_wrong_password_nobody_changed(jira):
    sync_tasks()
    assert jira.asked == 1

    run = sync_now()

    assert jira.asked == 1
    assert run.error == STILL_PAUSED
    assert sync_paused()


def test_sync_now_tries_again_once_the_login_changed(jira, settings):
    sync_tasks()
    settings.TRACKER_JIRA_PASSWORD = "new-password"
    jira.fails = None

    run = sync_now()

    assert run.outcome == SyncOutcome.SUCCEEDED
    assert TrackerSettings.load().sync_paused_at is None


def test_after_a_captcha_sync_now_tries_with_the_same_login(jira):
    jira.fails = TrackerCaptcha("Jira asks 'dsmirnov' for a captcha after wrong passwords.")
    sync_tasks()
    assert TrackerSettings.load().sync_paused_captcha
    # The PM signed in to Jira in a browser and solved the captcha.
    jira.fails = None

    run = sync_now()

    assert jira.asked == 2
    assert run.outcome == SyncOutcome.SUCCEEDED
    assert TrackerSettings.load().sync_paused_at is None


def test_while_a_captcha_waits_the_scheduled_runs_stay_paused(jira):
    jira.fails = TrackerCaptcha("Jira asks 'dsmirnov' for a captcha after wrong passwords.")
    sync_tasks()

    assert sync_paused()


def test_the_captcha_banner_sends_the_pm_to_jira_in_a_browser(jira, client, pm):
    jira.fails = TrackerCaptcha("Jira asks 'dsmirnov' for a captcha after wrong passwords.")
    sync_tasks()
    client.force_login(pm)

    page = client.get(reverse("logs")).content.decode()

    assert "Once you have signed in to Jira in a browser, press Sync now." in page
    assert "Fix the login in" not in page


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


def test_with_no_linked_tasks_the_login_is_still_checked_before_the_pause_lifts(jira):
    jira.fails = TrackerCaptcha("Jira asks 'dsmirnov' for a captcha after wrong passwords.")
    sync_tasks()

    still = sync_now()

    assert jira.asked == 2
    assert still.outcome == SyncOutcome.FAILED
    assert sync_paused()


def test_only_jira_not_answering_is_retried_soon(jira):
    jira.fails = TrackerRefused("Jira said no: the search is malformed.")

    run = sync_tasks()

    assert seconds_until_next(run, EVERY, retried=False) == (EVERY, False)


def test_a_rejected_login_while_asking_about_a_missing_task_pauses_the_sync(jira, monkeypatch, pm):
    from apps.accounts.models import Company, User
    from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded
    from apps.requests.services import create_request

    atlas = Company.objects.create(name="Atlas", owner=pm, jira_project="FI")
    anna = User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")
    request = create_request(
        anna, RequestType.BUG, "Leads", "Context", WhenNeeded.HOTFIX, link="https://x.test"
    )
    Request.objects.filter(pk=request.pk).update(jira_key="FI-2207", status=ClientStatus.PLANNED)

    class SearchWorksIssueRejected(Jira):
        def issues(self, keys: list[str]) -> list[Issue]:
            return []

        def issue(self, key: str) -> Issue:
            raise TrackerSignInFailed("Jira did not accept the login 'dimond-sync'.")

    monkeypatch.setattr("apps.tracker.services.current_tracker", SearchWorksIssueRejected)

    run = sync_tasks()

    assert run.outcome == SyncOutcome.FAILED
    assert sync_paused()
    request.refresh_from_db()
    assert request.jira_key == "FI-2207"
