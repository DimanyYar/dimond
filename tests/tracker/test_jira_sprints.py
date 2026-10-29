"""Sprints for a new task (PRD: Jira sync, "Create task"; roadmap #29) through Jira Software's
Agile API, against saved Jira 8.13 answers. No test here reaches Jira."""

import json

import httpx
import pytest

from apps.tracker.base import IssueNotFound, Sprint, TrackerRefused
from apps.tracker.jira import JiraTracker

from .test_jira import URL, saved

AGILE = "/rest/agile/1.0/"


def agile_answering(
    routes: dict[str, httpx.Response], seen: list[httpx.Request] | None = None
) -> JiraTracker:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return routes.get(request.url.path.removeprefix(AGILE), httpx.Response(404, json={}))

    return JiraTracker(URL, "dsmirnov", "secret", transport=httpx.MockTransport(handle))


def page(values: list[dict[str, object]], last: bool = True) -> httpx.Response:
    return httpx.Response(200, json={"isLast": last, "values": values})


def test_open_sprints_of_every_scrum_board_are_listed_once():
    seen: list[httpx.Request] = []
    jira = agile_answering(
        {
            "board": saved("board_FI.json"),
            "board/412/sprint": saved("board_412_sprints.json"),
            "board/418/sprint": saved("board_418_sprints.json"),
        },
        seen,
    )

    sprints = jira.open_sprints("FI")

    assert sprints == [
        Sprint(1931, "Sprint 12", "FI board"),
        Sprint(1874, "Bugs bucket", "FI board"),
        Sprint(1902, "Mobile bucket", "FI mobile"),
    ]
    assert seen[0].url.params["projectKeyOrId"] == "FI"
    assert seen[0].url.params["type"] == "scrum"
    assert seen[1].url.params["state"] == "active,future"


def test_a_project_without_a_scrum_board_has_no_sprints():
    jira = agile_answering({"board": page([])})

    assert jira.open_sprints("NW") == []


def test_sprints_are_read_page_after_page():
    seen: list[httpx.Request] = []
    pages = iter(
        [
            page([{"id": 1, "name": "Bucket 1"}], last=False),
            page([{"id": 2, "name": "Bucket 2"}]),
        ]
    )

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/board"):
            return page([{"id": 412, "name": "FI board"}])
        return next(pages)

    jira = JiraTracker(URL, "dsmirnov", "secret", transport=httpx.MockTransport(handle))

    assert [sprint.name for sprint in jira.open_sprints("FI")] == ["Bucket 1", "Bucket 2"]
    assert seen[2].url.params["startAt"] == "1"


def test_a_task_is_moved_into_the_chosen_sprint():
    seen: list[httpx.Request] = []
    jira = agile_answering({"sprint/1874/issue": httpx.Response(204)}, seen)

    jira.move_to_sprint("FI-2190", 1874)

    assert seen[0].method == "POST"
    assert json.loads(seen[0].content) == {"issues": ["FI-2190"]}


def test_jira_refusing_the_sprint_explains_why():
    jira = agile_answering(
        {
            "sprint/1874/issue": httpx.Response(
                400, json={"errorMessages": ["Issues can't be moved to a closed sprint."]}
            )
        }
    )

    with pytest.raises(TrackerRefused, match="closed sprint"):
        jira.move_to_sprint("FI-2190", 1874)


def test_a_deleted_sprint_is_reported_as_gone():
    jira = agile_answering({})

    with pytest.raises(TrackerRefused, match="sprint is gone"):
        jira.move_to_sprint("FI-2190", 1874)


def test_a_key_that_is_not_a_task_key_never_reaches_jira():
    seen: list[httpx.Request] = []
    jira = agile_answering({}, seen)

    with pytest.raises(IssueNotFound):
        jira.move_to_sprint("FI 1; drop", 1874)
    assert seen == []
