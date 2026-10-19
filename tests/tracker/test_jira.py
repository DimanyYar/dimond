"""The real Jira adapter against a fake network that answers with saved Jira 8.13 responses.
No test here reaches Jira."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from apps.tracker.base import (
    IssueNotFound,
    ProjectNotFound,
    TrackerForbidden,
    TrackerNotConfigured,
    TrackerSignInFailed,
    TrackerUnreachable,
)
from apps.tracker.jira import JiraTracker

RESPONSES = Path(__file__).parent / "jira_responses"
URL = "https://j.shakuro.com"

Handler = Callable[[httpx.Request], httpx.Response]


def saved(name: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=json.loads((RESPONSES / name).read_text()))


def jira_answering(routes: dict[str, httpx.Response], seen: list[httpx.Request]) -> JiraTracker:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path.removeprefix("/rest/api/2/")
        return routes.get(path, saved("issue_does_not_exist.json", 404))

    return JiraTracker(URL, "dsmirnov", "secret", transport=httpx.MockTransport(handle))


def jira_with(handler: Handler) -> JiraTracker:
    return JiraTracker(URL, "dsmirnov", "secret", transport=httpx.MockTransport(handler))


def test_reads_who_we_are_and_which_projects_we_see():
    seen: list[httpx.Request] = []
    jira = jira_answering(
        {"myself": saved("myself.json"), "project": saved("project_list.json")}, seen
    )

    assert jira.account().display_name == "Dmitry Smirnov"
    assert [(p.key, p.name) for p in jira.projects()] == [("FI", "Atlas"), ("NW", "Northwind")]
    assert seen[0].headers["Authorization"].startswith("Basic ")


def test_issue_types_leave_out_subtasks():
    jira = jira_answering({"project/FI": saved("project_FI.json")}, [])

    assert jira.issue_types("FI") == ["Story", "Bug", "Task", "Epic"]


def test_open_tasks_ask_jira_only_for_unfinished_tasks_of_the_project():
    seen: list[httpx.Request] = []
    jira = jira_answering({"search": saved("search_open_FI.json")}, seen)

    issues = jira.open_issues("FI")

    assert [i.key for i in issues] == ["FI-2231", "FI-2208"]
    assert issues[0].url == "https://j.shakuro.com/browse/FI-2231"
    jql = seen[0].url.params["jql"]
    assert jql == 'project = "FI" AND statusCategory != Done ORDER BY created DESC'


def test_words_search_drops_characters_jira_reads_as_operators():
    seen: list[httpx.Request] = []
    jira = jira_answering({"search": saved("search_open_FI.json")}, seen)

    jira.open_issues("FI", 'form - "duplicates"?')

    assert 'summary ~ "form    duplicates"' in seen[0].url.params["jql"]


def test_a_typed_key_finds_that_open_task_of_the_project():
    jira = jira_answering(
        {
            "issue/FI-2190": saved("issue_FI-2190.json"),
            "issue/FI-2150": saved("issue_FI-2150.json"),
        },
        [],
    )

    assert [i.key for i in jira.open_issues("FI", "fi-2190")] == ["FI-2190"]
    assert jira.open_issues("FI", "FI-2150") == []  # done
    assert jira.open_issues("NW", "FI-2190") == []  # another project
    assert jira.open_issues("FI", "FI-9999") == []  # no such task


def test_a_missing_task_says_so():
    jira = jira_answering({}, [])

    with pytest.raises(IssueNotFound, match="There is no task FI-9999"):
        jira.issue("FI-9999")


def test_a_project_key_never_goes_into_a_query_unchecked():
    seen: list[httpx.Request] = []
    jira = jira_answering({}, seen)

    with pytest.raises(ProjectNotFound):
        jira.open_issues('FI" OR project = "HR')
    assert seen == []


def test_wrong_password_is_reported_as_a_failed_sign_in():
    jira = jira_with(lambda request: httpx.Response(401))

    with pytest.raises(TrackerSignInFailed, match="did not accept the login 'dsmirnov'"):
        jira.account()


def test_captcha_after_wrong_passwords_is_explained():
    jira = jira_with(
        lambda request: httpx.Response(
            403, headers={"X-Authentication-Denied-Reason": "CAPTCHA_CHALLENGE; login-url=x"}
        )
    )

    with pytest.raises(TrackerSignInFailed, match="captcha"):
        jira.account()


def test_missing_permission_is_not_mistaken_for_a_wrong_password():
    jira = jira_with(lambda request: httpx.Response(403))

    with pytest.raises(TrackerForbidden):
        jira.projects()


def test_redirect_is_not_followed_so_the_password_goes_nowhere_else():
    seen: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "http://j.shakuro.com/rest/api/2/myself"})

    with pytest.raises(TrackerUnreachable, match="put that address in TRACKER_JIRA_URL"):
        jira_with(redirect).account()
    assert len(seen) == 1


def test_jira_that_does_not_answer_is_reported():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(TrackerUnreachable, match="did not answer"):
        jira_with(down).account()


def test_jira_errors_are_reported_with_their_code():
    with pytest.raises(TrackerUnreachable, match="answered 500"):
        jira_with(lambda request: httpx.Response(500)).projects()


def test_http_address_is_refused_because_the_password_would_travel_in_plain_text():
    with pytest.raises(TrackerNotConfigured, match="https://"):
        JiraTracker("http://j.shakuro.com", "dsmirnov", "secret")
