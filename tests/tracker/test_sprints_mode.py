"""The Sprints mode of the Releases page (#30, 30d; canvas 7 · PMReleaseView, "Plan by:
Sprints"): the client's sprints board by board, the chosen one's tasks in the same table with a
Release column. The copy comes from the demo tracker; no test reaches Jira."""

import pytest
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.tracker.mock import MockTracker
from apps.tracker.models import TrackerSprint
from apps.tracker.selectors import PlanBy, SprintKind, releases_page
from apps.tracker.services import copy_releases

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    company = Company.objects.create(name="Atlas", owner=pm, jira_project="FI")
    copy_releases(MockTracker())
    return company


def sprints(pm, sprint=None):
    return releases_page(pm, None, None, plan_by=PlanBy.SPRINTS, sprint=sprint)


def test_sprints_are_listed_board_by_board_active_next_later_then_closed(pm, atlas):
    page = sprints(pm)

    assert [(s.group, s.name, s.kind) for s in page.releases] == [
        ("FI board", "Sprint 12", SprintKind.ACTIVE),
        ("FI board", "Bugs bucket", SprintKind.NEXT),
        ("FI board", "Ideas bucket", SprintKind.LATER),
        ("FI board", "Old bucket", SprintKind.LATER),
        ("FI board", "Sprint 11", SprintKind.CLOSED),
        ("No sprint", "Not in a sprint", SprintKind.NONE),
    ]


def test_the_active_sprint_is_chosen_first_with_its_tasks_also_without_a_release(pm, atlas):
    page = sprints(pm)

    assert page.selected.name == "Sprint 12"
    assert page.selected.date_line == "Active sprint · Oct 20 – Nov 10"
    assert sorted(t.key for t in page.selected.tasks) == ["FI-2208", "FI-2214", "FI-2260"]


def test_the_table_shows_each_tasks_release_instead_of_its_sprint(pm, atlas):
    table = sprints(pm).table

    assert table.when_column == "Release"
    assert {r.task.key: r.when for r in table.rows} == {
        "FI-2208": "26.8.0",
        "FI-2214": "26.8.0",
        "FI-2260": "No release",
    }


def test_a_later_future_sprint_is_named_but_shows_no_tasks(pm, atlas):
    bucket = TrackerSprint.objects.get(name="Ideas bucket")

    selected = sprints(pm, str(bucket.pk)).selected

    assert (selected.tasks, selected.tasks_read) == ([], False)
    assert selected.date_line == "Future sprint · dimond doesn’t read its tasks"


def test_tasks_in_no_sprint_have_their_own_item(pm, atlas):
    selected = sprints(pm, "none").selected

    assert "FI-2231" in [t.key for t in selected.tasks]
    assert "FI-2214" not in [t.key for t in selected.tasks]


def test_filter_links_keep_the_sprints_mode_and_the_sprint(pm, atlas):
    sprint = TrackerSprint.objects.get(name="Sprint 12")

    links = {link.label: link.query for link in sprints(pm, str(sprint.pk)).table.filters}

    assert links["Not done"] == f"client={atlas.pk}&mode=sprints&sprint={sprint.pk}&show=open"


def test_the_page_switches_between_releases_and_sprints(client, pm, atlas):
    client.force_login(pm)

    page = client.get(reverse("releases"), {"mode": "sprints"}).content.decode()

    assert "Sprint 12" in page
    assert "Remove unused lead fields" in page
    assert f'href="?client={atlas.pk}" ' in page or f'href="?client={atlas.pk}"' in page
    assert ">Release</th>" in page
    assert "Hide this release" not in page


def test_another_pms_sprints_never_show_up(client, atlas):
    other = User.objects.create_user("olga@shakuro.com", name="Olga", role=Role.PM)
    client.force_login(other)

    page = client.get(reverse("releases"), {"mode": "sprints"}).content.decode()

    assert "Sprint 12" not in page


def test_a_wrong_mode_or_sprint_in_the_address_falls_back(client, pm, atlas):
    client.force_login(pm)

    page = client.get(reverse("releases"), {"mode": "nope", "sprint": "²"}).content.decode()

    assert "26.8.0" in page
