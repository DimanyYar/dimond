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
    TrackerCaptcha,
    TrackerForbidden,
    TrackerNotConfigured,
    TrackerRefused,
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
    jira = jira_answering(
        {"project/FI": saved("project_FI.json"), "search": saved("search_open_FI.json")}, seen
    )

    issues = jira.open_issues("FI")

    assert [i.key for i in issues] == ["FI-2231", "FI-2208"]
    assert issues[0].url == "https://j.shakuro.com/browse/FI-2231"
    jql = seen[-1].url.params["jql"]
    assert jql == 'project = "FI" AND statusCategory != Done ORDER BY created DESC'


def test_words_search_drops_characters_jira_reads_as_operators():
    seen: list[httpx.Request] = []
    jira = jira_answering(
        {"project/FI": saved("project_FI.json"), "search": saved("search_open_FI.json")}, seen
    )

    jira.open_issues("FI", 'form - "duplicates"?')

    assert 'summary ~ "form    duplicates"' in seen[-1].url.params["jql"]


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

    # A captcha is a failed sign-in of its own kind: the password may be right by now.
    with pytest.raises(TrackerCaptcha, match="captcha"):
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


def test_a_page_that_is_not_json_is_reported_not_crashed_on():
    proxy_login = lambda request: httpx.Response(200, text="<html>Sign in to the proxy</html>")  # noqa: E731

    with pytest.raises(TrackerUnreachable, match="not JSON"):
        jira_with(proxy_login).account()


def test_an_answer_in_an_unexpected_shape_is_reported_not_crashed_on():
    with pytest.raises(TrackerUnreachable, match="unexpected shape"):
        jira_with(
            lambda request: httpx.Response(200, json={"issues": [{"key": "FI-1"}]})
        ).open_issues("FI")


def test_searching_an_unknown_project_says_the_project_is_missing():
    seen: list[httpx.Request] = []
    jira = jira_answering({}, seen)

    with pytest.raises(ProjectNotFound, match="There is no project ZZ"):
        jira.open_issues("ZZ")
    assert all(request.url.path.endswith("/project/ZZ") for request in seen)


def test_a_rejected_search_is_not_mistaken_for_a_missing_project():
    jira = jira_answering(
        {
            "project/FI": saved("project_FI.json"),
            "search": httpx.Response(400, json={"errorMessages": ["Error in the JQL Query"]}),
        },
        [],
    )

    with pytest.raises(TrackerRefused, match="Jira said no: Error in the JQL Query"):
        jira.open_issues("FI", "duplicates")


def test_a_new_task_is_created_in_the_project_with_its_label():
    seen: list[httpx.Request] = []
    jira = jira_answering(
        {"issue": httpx.Response(201, json={"id": "47890", "key": "FI-2190"})}, seen
    )

    created = jira.create_issue("FI", "Story", "Lead card: source", "h3. What happens", ["dimond"])

    assert (created.key, created.url) == ("FI-2190", "https://j.shakuro.com/browse/FI-2190")
    assert len(seen) == 1  # no second call that could fail after the task exists
    sent = json.loads(seen[0].content)["fields"]
    assert sent == {
        "project": {"key": "FI"},
        "issuetype": {"name": "Story"},
        "summary": "Lead card: source",
        "description": "h3. What happens",
        "labels": ["dimond"],
    }


def test_jira_saying_no_to_a_new_task_explains_why():
    jira = jira_answering(
        {
            "issue": httpx.Response(
                400,
                json={"errorMessages": [], "errors": {"components": "Component/s is required."}},
            )
        },
        [],
    )

    with pytest.raises(TrackerRefused, match="Jira said no: Component/s is required."):
        jira.create_issue("FI", "Bug", "Duplicate leads", "", ["dimond"])


def test_an_epic_gets_its_required_epic_name_from_the_summary():
    seen: list[httpx.Request] = []
    jira = jira_answering(
        {
            "field": httpx.Response(
                200,
                json=[
                    {"id": "summary", "name": "Summary", "schema": {"type": "string"}},
                    {
                        "id": "customfield_10004",
                        "name": "Epic Name",
                        "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-label"},
                    },
                ],
            ),
            "issue": httpx.Response(201, json={"id": "47890", "key": "FI-2190"}),
        },
        seen,
    )

    jira.create_issue("FI", "Epic", "Lead sources", "", ["dimond"])

    created = next(r for r in seen if r.method == "POST")
    assert json.loads(created.content)["fields"]["customfield_10004"] == "Lead sources"


def test_a_file_goes_onto_a_task_as_an_upload_jira_accepts():
    seen: list[httpx.Request] = []
    jira = jira_answering({"issue/FI-2190/attachments": httpx.Response(200, json=[])}, seen)

    jira.attach_file("FI-2190", "duplicates.png", b"\x89PNG", "image/png")

    assert seen[0].headers["X-Atlassian-Token"] == "no-check"
    assert b'filename="duplicates.png"' in seen[0].content


def test_a_file_over_jiras_limit_is_explained():
    jira = jira_answering({"issue/FI-2190/attachments": httpx.Response(413)}, [])

    with pytest.raises(TrackerRefused, match="larger than its attachment limit"):
        jira.attach_file("FI-2190", "huge.png", b"x", "image/png")


def test_a_comment_is_added_to_a_task():
    seen: list[httpx.Request] = []
    jira = jira_answering({"issue/FI-2190/comment": httpx.Response(201, json={"id": "1"})}, seen)

    jira.add_comment("FI-2190", "From dimond: DMN-12")

    assert seen[0].method == "POST"
    assert json.loads(seen[0].content) == {"body": "From dimond: DMN-12"}


def test_linked_tasks_are_read_with_their_current_status():
    seen: list[httpx.Request] = []
    jira = jira_answering({"search": saved("search_linked.json")}, seen)

    issues = jira.issues(["FI-2214", "FI-2150"])

    assert [(i.key, i.status, i.done) for i in issues] == [
        ("FI-2214", "Code review", False),
        ("FI-2150", "Done", True),
    ]
    assert seen[0].url.params["jql"] == "key in (FI-2214,FI-2150)"


def test_a_deleted_task_is_left_out_instead_of_failing_the_whole_search():
    seen: list[httpx.Request] = []
    jira = jira_answering({"search": saved("search_linked.json")}, seen)

    issues = jira.issues(["FI-2214", "FI-2207", "FI-2150"])

    assert "FI-2207" not in [i.key for i in issues]
    assert seen[0].url.params["validateQuery"] == "false"


def test_a_task_moved_to_another_project_comes_back_under_its_new_key():
    jira = jira_answering({"search": saved("search_linked_moved.json")}, [])

    [moved] = jira.issues(["FI-2207"])

    assert (moved.key, moved.project_key, moved.status) == ("NW-90", "NW", "In Progress")


def test_many_linked_tasks_are_read_fifty_per_search():
    seen: list[httpx.Request] = []
    jira = jira_answering({"search": saved("search_linked.json")}, seen)

    jira.issues([f"FI-{n}" for n in range(1, 121)])

    assert [len(r.url.params["jql"].split(",")) for r in seen] == [50, 50, 20]


def test_all_pages_are_read_when_jira_gives_fewer_tasks_per_page_than_asked():
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = int(request.url.params["startAt"]) + 1
        return saved(f"search_linked_page_{page}.json")

    issues = jira_with(handle).issues(["FI-2214", "FI-2150"])

    assert [i.key for i in issues] == ["FI-2214", "FI-2150"]
    assert [r.url.params["startAt"] for r in seen] == ["0", "1"]


def test_a_malformed_key_never_goes_into_the_search():
    seen: list[httpx.Request] = []
    jira = jira_answering({"search": saved("search_linked.json")}, seen)

    jira.issues(["FI-2214", "FI-1) OR (project = HR"])

    assert seen[0].url.params["jql"] == "key in (FI-2214)"


def test_no_linked_tasks_asks_jira_nothing():
    seen: list[httpx.Request] = []

    assert jira_answering({}, seen).issues([]) == []
    assert seen == []


def test_fix_versions_with_a_release_date_come_soonest_first():
    jira = jira_answering({"search": saved("search_linked_releases.json")}, [])

    [task] = jira.issues(["FI-2214"])

    assert [(r.name, r.release_date.isoformat()) for r in task.releases] == [
        ("2.15", "2026-10-28"),
        ("2.16", "2026-11-12"),
    ]
