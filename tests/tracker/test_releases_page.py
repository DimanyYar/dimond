"""Releases page, part 1 (#30, 30b; canvas 7 · PMReleaseView): the PM sees each client's
releases grouped as Open, Hotfixes, Released and Buckets, the chosen one's dates, progress
counted from subtasks (D-004), subtasks by track and warnings, and can hide a release from the
list only in dimond (D-006). The copy comes from the demo tracker; no test reaches Jira."""

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Company, Role, User
from apps.requests.models import Request
from apps.tracker.mock import MockTracker
from apps.tracker.models import TrackerTask, TrackerVersion
from apps.tracker.selectors import ReleaseGroup, releases_page
from apps.tracker.services import copy_releases

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    company = Company.objects.create(name="Atlas", owner=pm, jira_project="FI")
    copy_releases(MockTracker())
    return company


def version(name: str) -> TrackerVersion:
    return TrackerVersion.objects.get(name=name)


def release(pm: User, name: str):
    page = releases_page(pm, None, version(name).pk)
    assert page.selected is not None
    return page.selected


def open_page(client, user: User, query: str = "") -> str:
    client.force_login(user)
    response = client.get(reverse("releases") + query)
    assert response.status_code == 200
    return response.content.decode()


def test_only_a_pm_opens_releases(client, atlas):
    person = User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")
    client.force_login(person)

    assert client.get(reverse("releases")).status_code == 404


def test_releases_are_grouped_open_hotfixes_released_and_buckets(pm, atlas):
    page = releases_page(pm, None, None)

    groups = [(r.group, r.version.name) for r in page.shown]
    assert groups == [
        (ReleaseGroup.OPEN, "25.8.0"),
        (ReleaseGroup.OPEN, "26.8.0"),
        (ReleaseGroup.OPEN, "26.8.5"),
        (ReleaseGroup.OPEN, "26.9.0"),
        (ReleaseGroup.HOTFIXES, "Hot Fix"),
        (ReleaseGroup.RELEASED, "26.7.0"),
        (ReleaseGroup.RELEASED, "26.6.0"),
    ]


def test_unreleased_is_hidden_by_default_and_can_be_shown(client, pm, atlas):
    assert [r.version.name for r in releases_page(pm, None, None).hidden] == ["Unreleased"]
    client.force_login(pm)

    client.post(reverse("release_visibility", args=[version("Unreleased").pk]), {"hidden": "0"})

    page = releases_page(pm, None, None)
    assert page.hidden == []
    assert (ReleaseGroup.BUCKETS, "Unreleased") in [(r.group, r.version.name) for r in page.shown]


def test_a_hidden_release_leaves_the_list_and_the_next_sync_keeps_it_hidden(client, pm, atlas):
    client.force_login(pm)

    response = client.post(
        reverse("release_visibility", args=[version("26.8.5").pk]), {"hidden": "1"}
    )
    copy_releases(MockTracker())

    assert response.status_code == 302
    page = releases_page(pm, None, None)
    assert "26.8.5" not in [r.version.name for r in page.shown]
    assert [r.version.name for r in page.hidden] == ["26.8.5", "Unreleased"]


def test_another_pm_cannot_hide_a_release_of_my_client(client, atlas):
    other = User.objects.create_user("olga@shakuro.com", name="Olga", role=Role.PM)
    client.force_login(other)

    response = client.post(
        reverse("release_visibility", args=[version("26.8.0").pk]), {"hidden": "1"}
    )

    assert response.status_code == 404
    assert not hasattr(version("26.8.0"), "visibility")


def test_another_pms_clients_never_show_up(client, atlas):
    other = User.objects.create_user("olga@shakuro.com", name="Olga", role=Role.PM)

    page = open_page(client, other)

    assert "No client has a Jira project yet" in page
    assert "26.8.0" not in page


def test_the_first_open_release_is_chosen_when_none_is_asked_for(pm, atlas):
    assert releases_page(pm, None, None).selected.version.name == "25.8.0"


def test_a_release_starts_the_day_after_the_previous_one(pm, atlas):
    assert release(pm, "26.8.0").date_line == "Starts Oct 1 · no release date yet"


def test_a_release_after_one_without_a_date_starts_when_that_one_ships(pm, atlas):
    assert release(pm, "26.9.0").date_line == (
        "Starts the day after 26.8.5 ships · no release date yet"
    )


def test_the_jira_start_date_wins_over_the_automatic_one(pm, atlas):
    TrackerVersion.objects.filter(name="26.8.0").update(start_date=date(2026, 10, 5))

    assert release(pm, "26.8.0").date_line.startswith("Starts Oct 5")


def test_progress_comes_from_subtasks_not_the_storys_jira_status(pm, atlas):
    selected = release(pm, "26.8.0")

    # Both stories are To Do in Jira, but their subtasks are moving; the bug is To Do.
    assert (len(selected.tasks), selected.done, selected.in_progress) == (3, 0, 2)


def test_a_story_is_done_when_all_its_subtasks_are(pm, atlas):
    TrackerTask.objects.filter(parent__key="FI-2208").update(status_category="done")

    assert release(pm, "26.8.0").done == 1


def test_subtasks_are_counted_by_track(pm, atlas):
    tracks = {t.name: (t.done, t.total, t.in_progress) for t in release(pm, "26.8.0").tracks}

    assert tracks == {"FE": (0, 2, 2), "BE": (2, 2, 0), "QA": (0, 1, 0), "QA bug": (0, 1, 1)}


def test_a_release_without_a_date_warns_about_the_client_requests_in_it(pm, atlas):
    anna = User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")
    Request.objects.create(company=atlas, author=anna, title="Filter", jira_key="FI-2214")

    selected = release(pm, "26.8.0")

    assert selected.client_requests == 1
    assert selected.warnings[0] == (
        "No release date in Jira: 1 client request in it show no date for this release."
    )


def test_qa_not_started_on_tasks_in_progress_is_a_warning(pm, atlas):
    assert "QA hasn’t started on any task in progress; 1 QA subtask is To Do." in (
        release(pm, "26.8.0").warnings
    )


def test_a_story_without_subtasks_is_a_warning(pm, atlas):
    TrackerTask.objects.filter(parent__key="FI-2197").delete()

    assert (
        "“Export leads to CSV in the background” has no subtasks yet: nobody has planned the work."
        in release(pm, "26.9.0").warnings
    )


def test_an_open_release_with_a_lower_number_than_a_released_one_looks_forgotten(pm, atlas):
    selected = release(pm, "25.8.0")

    assert selected.health == "Stale"
    assert selected.date_line == "1 of 1 done, never released"
    assert selected.warnings[0].startswith("Looks forgotten: 1 of 1 done, 0 not done.")


def test_an_open_release_past_its_date_looks_forgotten(pm, atlas):
    TrackerVersion.objects.filter(name="26.8.0").update(
        release_date=timezone.localdate() - timedelta(days=1)
    )

    assert release(pm, "26.8.0").health == "Stale"


def test_an_empty_release_says_so(pm, atlas):
    selected = release(pm, "26.8.5")

    assert (selected.health, selected.warnings) == ("Empty", [])


def test_hot_fix_is_a_recent_list_not_a_release(pm, atlas):
    selected = release(pm, "Hot Fix")

    assert selected.health == "Last 30 days"
    assert [t.key for t in selected.tasks] == ["FI-2090"]
    assert selected.warnings == []


def test_the_page_shows_the_chosen_release_with_a_link_to_jira(client, pm, atlas):
    page = open_page(client, pm, f"?release={version('26.8.0').pk}")

    assert "Atlas · Jira project FI" in page
    assert "Subtasks by track" in page
    assert f"/projects/FI/versions/{version('26.8.0').jira_id}" in page
    assert "Hidden (1)" in page


def test_a_client_without_a_copy_yet_waits_for_the_next_sync(client, pm):
    Company.objects.create(name="Atlas", owner=pm, jira_project="FI")

    assert "No releases yet" in open_page(client, pm)


@pytest.mark.parametrize("value", ["²", "9" * 30, "abc"])
def test_a_wrong_client_or_release_in_the_address_opens_the_default_one(client, pm, atlas, value):
    page = open_page(client, pm, f"?client={value}&release={value}")

    assert "Atlas · Jira project FI" in page


def test_a_hotfix_release_added_later_in_jira_does_not_make_the_next_release_stale(pm, atlas):
    # In FI 26.7.5 is added after 26.8.0 in Jira's list and released first.
    TrackerVersion.objects.filter(name="26.7.0").update(position=99)

    assert release(pm, "26.8.0").health == "No date"
