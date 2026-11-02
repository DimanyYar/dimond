"""Every sync run copies the releases of each client's Jira project for the Releases page
(#30; ADR 0026): versions, their tasks and subtasks, linked to requests or not. Jira answers
from saved responses or the demo tracker; no test reaches Jira."""

from datetime import UTC, date, datetime

import httpx
import pytest

from apps.accounts.models import Company
from apps.tracker import mock
from apps.tracker.base import TrackerUnreachable
from apps.tracker.jira import JiraTracker
from apps.tracker.mock import MockTracker
from apps.tracker.models import SyncOutcome, TrackerTask, TrackerVersion
from apps.tracker.services import copy_releases, sync_tasks

from .test_jira import URL, saved


class ReleasesJira:
    """Jira answering the release reads; remembers what dimond asked."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path = request.url.path.removeprefix("/rest/api/2/")
        if path == "search":
            jql = request.url.params["jql"]
            name = "search_release_subtasks" if jql.startswith("parent") else "search_release_tasks"
            return saved(f"{name}.json")
        files = {"project/FI/versions": "project_FI_versions", "field": "field_list"}
        return saved(f"{files[path]}.json")

    def tracker(self) -> JiraTracker:
        return JiraTracker(URL, "dsmirnov", "secret", transport=httpx.MockTransport(self.handle))

    def jql(self) -> list[str]:
        return [r.url.params["jql"] for r in self.seen if r.url.path.endswith("/search")]


@pytest.fixture
def jira() -> ReleasesJira:
    return ReleasesJira()


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm, jira_project="FI")


def test_versions_come_with_their_dates_and_whether_they_are_out(jira):
    versions = jira.tracker().versions("FI")

    released = next(v for v in versions if v.name == "26.7.0")
    assert (released.start_date, released.release_date) == (date(2026, 8, 27), date(2026, 9, 30))
    assert released.released
    assert not released.archived
    assert next(v for v in versions if v.name == "26.8.0").release_date is None


def test_release_tasks_ask_for_whole_versions_and_recently_closed_hot_fixes(jira):
    jira.tracker().release_tasks("FI", ["20130"], ["20150"], date(2026, 10, 3))

    assert jira.jql()[0] == (
        'project = "FI" AND (fixVersion in (20130) OR '
        '(fixVersion in (20150) AND resolved >= "2026-10-03")) ORDER BY key'
    )
    assert "customfield_10004" in jira.seen[-1].url.params["fields"]


def test_subtasks_come_by_their_parents_keys(jira):
    tasks = {t.key: t for t in jira.tracker().release_tasks("FI", ["20130"], ["20150"], date.min)}

    assert jira.jql()[1] == "parent in (FI-2214,FI-2090) ORDER BY key"
    bug = tasks["FI-2244"]
    assert (bug.issue_type, bug.subtask, bug.parent_key, bug.version_ids) == (
        "Sub-bug",
        True,
        "FI-2214",
        (),
    )
    assert tasks["FI-2241"].assignee_name == "Olga Petrova"
    assert tasks["FI-2214"].assignee_login == ""
    assert tasks["FI-2214"].status_category == "new"


def test_sprints_are_read_from_the_tasks_sprint_field(jira):
    story = jira.tracker().release_tasks("FI", ["20130"], [], date.min)[0]

    closed, active = story.sprints
    assert (closed.id, closed.name, closed.state) == (412, "FI 11, leads", "closed")
    assert active.starts == datetime(2026, 10, 20, 7, tzinfo=UTC)


def test_no_version_to_read_asks_jira_nothing(jira):
    assert jira.tracker().release_tasks("FI", [], [], date.min) == []
    assert jira.seen == []


def test_copying_releases_only_reads_jira(jira, atlas):
    copy_releases(jira.tracker())

    assert {r.method for r in jira.seen} == {"GET"}


def test_only_open_versions_hot_fix_and_last_two_released_are_read(jira, atlas):
    copy_releases(jira.tracker())

    # 26.5.0 is older history; Unreleased is a hidden bucket; Hot Fix only lately closed.
    assert "fixVersion in (20130,20120,20110) OR (fixVersion in (20150)" in jira.jql()[0]
    assert not TrackerVersion.objects.filter(name="25.1.0").exists()  # archived
    assert TrackerVersion.objects.get(name="Unreleased").tasks.count() == 0


def test_sync_copies_releases_with_their_subtasks(atlas):
    run = sync_tasks()

    story = TrackerTask.objects.get(key="FI-2214")
    assert run.outcome == SyncOutcome.SUCCEEDED
    assert run.releases_copied == TrackerTask.objects.count()
    assert [v.name for v in story.versions.all()] == ["26.8.0"]
    assert sorted(story.subtasks.values_list("key", flat=True)) == [
        "FI-2241",
        "FI-2242",
        "FI-2243",
        "FI-2244",
    ]
    assert story.sprints.get(state="active").name == "Sprint 12"


def test_hot_fix_keeps_only_tasks_closed_in_the_last_30_days(atlas):
    copy_releases(MockTracker())

    hot_fix = TrackerVersion.objects.get(name="Hot Fix")
    assert list(hot_fix.tasks.values_list("key", flat=True)) == ["FI-2090"]


def test_task_gone_from_jira_leaves_the_copy_and_the_rest_keep_their_rows(atlas, monkeypatch):
    copy_releases(MockTracker())
    story = TrackerTask.objects.get(key="FI-2214")
    monkeypatch.setattr(
        mock, "RELEASE_TASKS", [t for t in mock.RELEASE_TASKS if t.key != "FI-2231"]
    )

    copy_releases(MockTracker())

    assert not TrackerTask.objects.filter(key="FI-2231").exists()
    assert TrackerTask.objects.get(key="FI-2214").pk == story.pk


def test_project_jira_fails_for_keeps_its_previous_copy(atlas, monkeypatch):
    copy_releases(MockTracker())
    before = TrackerTask.objects.count()

    def silent(self, project_key):
        raise TrackerUnreachable("Jira at https://jira.example.com did not answer.")

    monkeypatch.setattr(MockTracker, "versions", silent)
    run = sync_tasks()

    assert TrackerTask.objects.count() == before
    assert run.outcome == SyncOutcome.SUCCEEDED
    assert run.releases_error == "FI: Jira at https://jira.example.com did not answer."


def test_project_no_client_uses_any_more_leaves_the_copy(atlas):
    copy_releases(MockTracker())
    atlas.jira_project = ""
    atlas.save()

    copy_releases(MockTracker())

    assert not TrackerTask.objects.exists()
    assert not TrackerVersion.objects.exists()
