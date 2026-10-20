"""While the sync is paused for a rejected login, Create Jira task waits too: each try at
Jira's password brings Jira closer to locking the account (PRD: Jira sync)."""

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Company, User
from apps.requests import services
from apps.requests.models import Request, RequestType, WhenNeeded
from apps.tracker.connection import login_fingerprint
from apps.tracker.models import TrackerKind, TrackerSettings

pytestmark = pytest.mark.django_db


@pytest.fixture
def request_(pm) -> Request:
    atlas = Company.objects.create(name="Atlas", owner=pm, jira_project="FI")
    anna = User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")
    return services.create_request(
        anna, RequestType.IMPROVEMENT, "Filter by source", "Context", WhenNeeded.NEXT_RELEASE
    )


@pytest.fixture
def paused(settings) -> None:
    settings.TRACKER_JIRA_URL = "https://j.shakuro.com"
    settings.TRACKER_JIRA_USERNAME = "dimond-sync"
    settings.TRACKER_JIRA_PASSWORD = "wrong"
    TrackerSettings.objects.update_or_create(
        pk=1,
        defaults={
            "tracker": TrackerKind.JIRA,
            "sync_paused_at": timezone.now(),
            "sync_paused_reason": "Jira did not accept the login.",
            "sync_paused_login": login_fingerprint(),
        },
    )


def test_the_button_is_dimmed_with_a_way_to_logs(client, pm, request_, paused):
    client.force_login(pm)

    page = client.get(request_.get_absolute_url()).content.decode()

    assert 'type="button" disabled>' in page
    assert "Jira rejected dimond’s login" in page
    assert reverse("logs") in page
    assert reverse("request_jira_task", args=[request_.pk]) not in page


def test_the_window_does_not_open_by_its_address_either(client, pm, request_, paused):
    client.force_login(pm)

    assert client.get(reverse("request_jira_task", args=[request_.pk])).status_code == 403
    assert (
        client.get(reverse("request_jira_task", args=[request_.pk]) + "?mode=existing").status_code
        == 403
    )


def test_a_fixed_login_brings_the_button_back(client, pm, request_, paused, settings):
    settings.TRACKER_JIRA_PASSWORD = "right"
    client.force_login(pm)

    page = client.get(request_.get_absolute_url()).content.decode()

    assert reverse("request_jira_task", args=[request_.pk]) in page


def test_the_demo_tracker_is_never_held_back(client, pm, request_, paused):
    TrackerSettings.objects.filter(pk=1).update(tracker=TrackerKind.DEMO)
    client.force_login(pm)

    page = client.get(request_.get_absolute_url()).content.decode()

    assert reverse("request_jira_task", args=[request_.pk]) in page
