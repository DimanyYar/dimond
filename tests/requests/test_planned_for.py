""" "Planned for" on the card comes from the task's fix versions that have a release date
(PRD: Jira sync — дата релиза; decided Oct 19–20, 2026)."""

from datetime import date

import pytest
from django.core.management import call_command

from apps.accounts.models import Company, User
from apps.requests import services
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded
from apps.requests.selectors import request_page
from apps.tracker.base import Release

pytestmark = pytest.mark.django_db


@pytest.fixture
def anna(pm) -> User:
    atlas = Company.objects.create(name="Atlas", owner=pm, jira_project="FI")
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")


@pytest.fixture
def request_(anna, pm) -> Request:
    request = services.create_request(
        anna, RequestType.IMPROVEMENT, "Filter by source", "Context", WhenNeeded.NEXT_RELEASE
    )
    return services.link_jira_task(pm, request, "FI-2214", "")


def lines(user: User, request: Request) -> list[str]:
    return [entry.event for entry in request_page(user, request.pk).thread if entry.event]


def card(client, user: User, request: Request) -> str:
    client.force_login(user)
    return client.get(request.get_absolute_url()).content.decode()


V215 = Release("2.15", date(2026, 10, 28))
V216 = Release("2.16", date(2026, 11, 12))


def test_a_linked_task_without_a_dated_version_says_not_set_yet(client, anna, request_):
    page = card(client, anna, request_)

    assert "Planned for" in page
    assert "Not set yet" in page


def test_a_request_without_a_task_has_no_planned_for(client, anna):
    request = services.create_request(
        anna, RequestType.IDEA, "Dark mode", "Context", WhenNeeded.NICE_TO_HAVE
    )

    assert "Planned for" not in card(client, anna, request)


def test_the_first_release_date_reaches_the_client_with_a_line(client, anna, pm, request_):
    services.follow_releases(request_, (V215,))

    assert "2.15 · Oct 28" in card(client, anna, request_)
    assert "Planned for 2.15 · Oct 28" in lines(anna, request_)
    assert "Planned for 2.15 · Oct 28" in lines(pm, request_)


def test_every_dated_version_is_listed(anna, request_):
    services.follow_releases(request_, (V215, V216))

    request_.refresh_from_db()
    assert request_.planned_for == "2.15 · Oct 28, 2.16 · Nov 12"


def test_a_moved_date_says_moved(anna, request_):
    services.follow_releases(request_, (V215,))

    services.follow_releases(request_, (V216,))

    assert lines(anna, request_)[-2:] == ["Planned for 2.15 · Oct 28", "Moved to 2.16 · Nov 12"]


def test_a_date_taken_away_changes_the_card_quietly(client, anna, request_):
    services.follow_releases(request_, (V215,))

    assert services.follow_releases(request_, ()) is not None

    assert "Not set yet" in card(client, anna, request_)
    assert not any(line.startswith("Moved") for line in lines(anna, request_))


def test_the_same_versions_again_change_nothing(request_):
    services.follow_releases(request_, (V215,))

    assert services.follow_releases(request_, (V215,)) is None
    assert request_.plan_events.count() == 1


def test_the_demo_sync_brings_a_release_date(client, anna, pm):
    request = services.create_request(
        anna, RequestType.IMPROVEMENT, "Source campaign", "Context", WhenNeeded.THIS_RELEASE
    )
    services.link_jira_task(pm, request, "FI-2190", "")

    call_command("sync_tracker")

    request.refresh_from_db()
    assert request.status == ClientStatus.READY_TO_CHECK
    assert request.planned_for == "2.15 · Oct 28"
    assert "Planned for 2.15 · Oct 28" in lines(anna, request)
