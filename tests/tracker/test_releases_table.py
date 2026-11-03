"""The task table of a release (#30, 30c; canvas 7 · PMReleaseView): a row per task with its
subtasks under it, track chips, status counted from subtasks with what Jira says, people from
subtasks, the sprint, the client request, and filters. The copy comes from the demo tracker;
no test reaches Jira."""

from datetime import UTC, datetime

import pytest
from django.urls import reverse

from apps.accounts.models import Company, User
from apps.requests.models import Request
from apps.tracker.mock import MockTracker
from apps.tracker.models import TrackerTask, TrackerVersion
from apps.tracker.selectors import (
    TASK_TYPES,
    Chip,
    TableFilter,
    TaskState,
    releases_page,
    task_types,
)
from apps.tracker.services import copy_releases

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    company = Company.objects.create(name="Atlas", owner=pm, jira_project="FI")
    copy_releases(MockTracker())
    return company


@pytest.fixture
def request_for_story(atlas) -> Request:
    anna = User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")
    return Request.objects.create(company=atlas, author=anna, title="Filter", jira_key="FI-2208")


def table(pm, name="26.8.0", show=TableFilter.ALL, types=TASK_TYPES):
    version = TrackerVersion.objects.get(name=name)
    page = releases_page(pm, None, version.pk, show, types)
    assert page.table is not None
    return page.table


def row(pm, key, name="26.8.0"):
    return next(r for r in table(pm, name).rows if r.task.key == key)


def test_status_comes_from_subtasks_and_says_what_jira_says(pm, atlas):
    story = row(pm, "FI-2214")
    bug = row(pm, "FI-2231")

    assert (story.state, story.jira_says) == (TaskState.IN_PROGRESS, "To Do")
    assert (bug.state, bug.jira_says) == (TaskState.TO_DO, "")


def test_subtasks_show_as_track_chips(pm, atlas):
    assert row(pm, "FI-2214").chips == [
        Chip("FE 0/1", "moving"),
        Chip("BE 1/1", "done"),
        Chip("QA 0/1", "todo"),
        Chip("1 QA bug", "bug"),
    ]


def test_people_come_from_the_subtasks(pm, atlas):
    assert row(pm, "FI-2214").people == ["Ivan Kuznetsov", "Olga Petrova", "Anna Sokolova"]


def test_the_sprint_shown_is_the_active_one(pm, atlas):
    assert row(pm, "FI-2208").when == "Sprint 12"


def test_client_requests_come_first_then_tasks_in_progress(pm, atlas, request_for_story):
    keys = [r.task.key for r in table(pm).rows]

    assert keys == ["FI-2208", "FI-2214", "FI-2231"]
    assert table(pm).rows[0].request == request_for_story


def test_the_client_requests_filter_keeps_only_linked_tasks(pm, atlas, request_for_story):
    rows = table(pm, show=TableFilter.CLIENT).rows

    assert [r.task.key for r in rows] == ["FI-2208"]


def test_the_not_done_filter_hides_done_tasks(pm, atlas):
    TrackerTask.objects.filter(parent__key="FI-2208").update(status_category="done")

    assert "FI-2208" not in [r.task.key for r in table(pm, show=TableFilter.NOT_DONE).rows]


def test_task_types_can_be_switched_off(pm, atlas):
    rows = table(pm, types=task_types("Bug")).rows

    assert [r.task.key for r in rows] == ["FI-2231"]


@pytest.mark.parametrize(
    ("value", "types"),
    [(None, {"Story", "Task", "Bug"}), ("none", set()), ("Bug,Epic", {"Bug"})],
)
def test_task_types_are_read_from_the_address(value, types):
    assert task_types(value) == types


def test_a_story_without_subtasks_says_so(pm, atlas):
    TrackerTask.objects.filter(parent__key="FI-2197").delete()

    assert row(pm, "FI-2197", "26.9.0").unplanned


def test_hot_fix_lists_when_each_task_was_done(pm, atlas):
    TrackerTask.objects.filter(key="FI-2090").update(
        resolved_at=datetime(2026, 10, 28, 12, tzinfo=UTC)
    )
    hotfix = table(pm, "Hot Fix")

    assert hotfix.when_column == "Done"
    assert hotfix.rows[0].when == "done Oct 28"


def test_filter_links_keep_the_client_and_the_release(pm, atlas):
    links = {link.label: link.query for link in table(pm).filters}
    version = TrackerVersion.objects.get(name="26.8.0")

    assert links["Not done"] == f"client={atlas.pk}&release={version.pk}&show=open"


def test_the_page_shows_tasks_with_jira_links_and_hidden_subtasks(client, pm, atlas):
    request = Request.objects.create(
        company=atlas,
        author=User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K."),
        title="Filter",
        jira_key="FI-2214",
    )
    version = TrackerVersion.objects.get(name="26.8.0")
    client.force_login(pm)

    page = client.get(reverse("releases"), {"release": version.pk, "show": "client"}).content
    page = page.decode()

    assert "Filter the leads list by source" in page
    assert "/browse/FI-2214" in page
    assert '<tr class="subtask-row" hidden>' in page
    assert "Jira says To Do" in page
    assert request.key in page
    assert "Web form: leads created twice on submit" not in page


def test_a_wrong_filter_in_the_address_shows_all_tasks(client, pm, atlas):
    version = TrackerVersion.objects.get(name="26.8.0")
    client.force_login(pm)

    page = client.get(reverse("releases"), {"release": version.pk, "show": "nope"}).content

    assert "Web form: leads created twice on submit" in page.decode()
