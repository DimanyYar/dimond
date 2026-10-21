"""A Jira webhook wakes the sync so a change reaches the client in seconds; polling stays the
source of truth (PRD: Jira sync; ADR 0010, 0020). Nothing here reaches Jira: a fake tracker
answers the runs."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Company, User
from apps.requests import services
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded
from apps.tracker.base import Issue
from apps.tracker.connection import login_fingerprint
from apps.tracker.models import SyncOutcome, SyncRun, TrackerKind, TrackerSettings
from apps.tracker.services import ask_for_sync

pytestmark = pytest.mark.django_db

TOKEN = "s3cret-token-from-env"


class FakeJira:
    def __init__(self) -> None:
        self.statuses: dict[str, str] = {}

    def issues(self, keys: list[str]) -> list[Issue]:
        return [
            Issue(key, "", self.statuses[key], "Story", "FI", False, "")
            for key in keys
            if key in self.statuses
        ]


@pytest.fixture
def jira(monkeypatch, settings) -> FakeJira:
    settings.TRACKER_JIRA_WEBHOOK_TOKEN = TOKEN
    TrackerSettings.objects.update_or_create(pk=1, defaults={"tracker": TrackerKind.JIRA})
    fake = FakeJira()
    monkeypatch.setattr("apps.tracker.services.current_tracker", lambda: fake)
    return fake


@pytest.fixture
def anna(pm) -> User:
    atlas = Company.objects.create(name="Atlas", owner=pm, jira_project="FI")
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")


def linked(author: User, key: str, status: ClientStatus = ClientStatus.PLANNED) -> Request:
    request = services.create_request(
        author, RequestType.IMPROVEMENT, f"Request {key}", "Context", WhenNeeded.NEXT_RELEASE
    )
    Request.objects.filter(pk=request.pk).update(jira_key=key, jira_status="To Do", status=status)
    return request


def webhook(client, key: str = "FI-2214", token: str = TOKEN, body: str = "", method: str = "post"):
    url = reverse("jira_webhook", args=[token]) + (f"?key={key}" if key else "")
    return getattr(client, method)(url, body, content_type="application/json")


def queued() -> list[SyncRun]:
    return list(SyncRun.objects.filter(outcome=SyncOutcome.QUEUED))


def test_a_status_change_reaches_the_client_right_after_the_webhook(client, jira, anna):
    request = linked(anna, "FI-2214")
    jira.statuses["FI-2214"] = "Code review"

    response = webhook(client)

    assert response.status_code == 204
    [run] = queued()
    assert run.webhook_keys == ["FI-2214"]
    assert not run.manual

    call_command("sync_tracker")

    request.refresh_from_db()
    assert request.status == ClientStatus.IN_PROGRESS
    run.refresh_from_db()
    assert run.outcome == SyncOutcome.SUCCEEDED


def test_webhooks_arriving_while_a_run_waits_join_it(client, jira, anna):
    for key in ("FI-2214", "FI-2215", "FI-2216"):
        linked(anna, key)

    for key in ("FI-2214", "FI-2215", "FI-2214", "FI-2216"):
        webhook(client, key)

    [run] = queued()
    assert run.webhook_keys == ["FI-2214", "FI-2215", "FI-2216"]


def test_a_webhook_after_the_run_started_queues_the_next_one(client, jira, anna):
    linked(anna, "FI-2214")
    webhook(client)
    SyncRun.objects.update(outcome=SyncOutcome.RUNNING)

    webhook(client)

    assert len(queued()) == 1


def test_a_webhook_joins_a_waiting_sync_now_and_sync_now_joins_a_webhook_run(
    client, jira, anna, pm
):
    linked(anna, "FI-2214")
    webhook(client)

    ask_for_sync(pm)

    [run] = queued()
    assert run.manual
    assert run.webhook_keys == ["FI-2214"]


def test_a_task_dimond_does_not_follow_starts_no_run(client, jira, anna):
    linked(anna, "FI-2214")

    response = webhook(client, "SUB-77")

    assert response.status_code == 204
    assert not SyncRun.objects.exists()


def test_a_request_closed_long_ago_starts_no_run(client, jira, anna):
    linked(anna, "FI-2150", ClientStatus.DONE)
    Request.objects.update(updated_at=timezone.now() - timedelta(days=31))

    webhook(client, "FI-2150")

    assert not SyncRun.objects.exists()


def test_a_wrong_token_looks_like_a_missing_page(client, jira, anna):
    linked(anna, "FI-2214")

    response = webhook(client, token="guessed")

    assert response.status_code == 404
    assert not SyncRun.objects.exists()


def test_without_a_token_in_env_the_webhook_is_off(client, jira, anna, settings):
    linked(anna, "FI-2214")
    settings.TRACKER_JIRA_WEBHOOK_TOKEN = ""

    assert webhook(client, token="anything").status_code == 404
    assert not SyncRun.objects.exists()


def test_the_body_jira_sends_is_never_trusted(client, jira, anna):
    request = linked(anna, "FI-2214")
    jira.statuses["FI-2214"] = "To Do"
    forged = '{"issue": {"key": "FI-2214", "fields": {"status": {"name": "Done"}}}}'

    webhook(client, body=forged)
    call_command("sync_tracker")

    request.refresh_from_db()
    assert request.status == ClientStatus.PLANNED


def test_a_webhook_address_without_the_task_key_is_refused(client, jira, anna):
    linked(anna, "FI-2214")

    assert webhook(client, key="").status_code == 400
    assert webhook(client, key="${issue.key}").status_code == 400
    assert not SyncRun.objects.exists()


def test_the_webhook_takes_only_post(client, jira, anna):
    linked(anna, "FI-2214")

    assert webhook(client, method="get").status_code == 405
    assert not SyncRun.objects.exists()


def test_a_webhook_while_the_sync_is_paused_is_ignored(client, jira, anna, settings):
    settings.TRACKER_JIRA_URL = "https://j.shakuro.com"
    settings.TRACKER_JIRA_USERNAME = "dimond-sync"
    settings.TRACKER_JIRA_PASSWORD = "wrong-password"
    linked(anna, "FI-2214")
    TrackerSettings.objects.filter(pk=1).update(
        sync_paused_at=timezone.now(),
        sync_paused_reason="Jira did not accept the login 'dimond-sync'.",
        sync_paused_login=login_fingerprint(),
    )

    response = webhook(client)

    assert response.status_code == 204
    assert not SyncRun.objects.exists()


def test_with_the_demo_tracker_a_webhook_starts_no_run(client, jira, anna):
    linked(anna, "FI-2214")
    TrackerSettings.objects.filter(pk=1).update(tracker=TrackerKind.DEMO)

    webhook(client)

    assert not SyncRun.objects.exists()
