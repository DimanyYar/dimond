"""Jira Server 8.13 through REST API v2 with Basic Auth (PRD: Jira sync; ADR 0008, 0017).

Personal access tokens arrived only in Jira 8.14, so the account's password goes with every
request. That is why the address must be https and redirects are never followed: Jira reports
its own base URL as http, and a redirect would send the password to wherever it points.
"""

import re
from collections.abc import Callable
from typing import Any

import httpx

from .base import (
    ISSUE_KEY,
    PROJECT_KEY,
    Account,
    Issue,
    IssueNotFound,
    Project,
    ProjectNotFound,
    TrackerError,
    TrackerForbidden,
    TrackerNotConfigured,
    TrackerRefused,
    TrackerSignInFailed,
    TrackerUnreachable,
)

TIMEOUT_SECONDS = 15
OPEN_ISSUES_LIMIT = 20
ISSUE_FIELDS = "summary,status,issuetype,project"
# Jira's text search treats these as operators and answers 400 to a stray one; a PM typing
# "form - duplicates?" means the words, not the operators.
TEXT_SEARCH_OPERATORS = re.compile(r"[^\w\s]")
# Jira Software keeps an epic's required short name in a custom field whose number differs
# from one Jira to another; its type is what identifies it.
EPIC_NAME_TYPE = "com.pyxis.greenhopper.jira:gh-epic-label"


class JiraTracker:
    name = "Jira"

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not url.startswith("https://"):
            raise TrackerNotConfigured(
                f"TRACKER_JIRA_URL must start with https:// (now {url!r}): over http the "
                "password would travel in plain text."
            )
        self.address = url.rstrip("/")
        self.username = username
        self._client = httpx.Client(
            base_url=f"{self.address}/rest/api/2/",
            auth=(username, password),
            headers={"Accept": "application/json"},
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
            transport=transport,
        )

    def account(self) -> Account:
        return self._call(
            "myself", lambda data: Account(login=data["name"], display_name=data["displayName"])
        )

    def projects(self) -> list[Project]:
        return self._call(
            "project",
            lambda data: sorted(
                (Project(key=item["key"], name=item["name"]) for item in data),
                key=lambda p: p.key,
            ),
        )

    def issue_types(self, project_key: str) -> list[str]:
        return self._call(
            f"project/{self._checked_project(project_key)}",
            lambda data: [item["name"] for item in data["issueTypes"] if not item["subtask"]],
            missing=ProjectNotFound(project_key),
        )

    def open_issues(self, project_key: str, text: str = "") -> list[Issue]:
        project_key = self._checked_project(project_key)
        text = text.strip()
        if ISSUE_KEY.fullmatch(text.upper()):
            return self._open_issue_by_key(project_key, text.upper())
        # Jira answers a search in an unknown project with the same 400 as a broken query, so
        # the project is asked for first: only a missing project becomes ProjectNotFound.
        self.issue_types(project_key)
        jql = f'project = "{project_key}" AND statusCategory != Done'
        words = TEXT_SEARCH_OPERATORS.sub(" ", text).strip()
        if words:
            jql += f' AND summary ~ "{words}"'
        return self._call(
            "search",
            lambda data: [self._issue(item) for item in data["issues"]],
            {
                "jql": f"{jql} ORDER BY created DESC",
                "fields": ISSUE_FIELDS,
                "maxResults": OPEN_ISSUES_LIMIT,
            },
        )

    def issue(self, key: str) -> Issue:
        if not ISSUE_KEY.fullmatch(key):
            raise IssueNotFound(key)
        return self._call(
            f"issue/{key}", self._issue, {"fields": ISSUE_FIELDS}, missing=IssueNotFound(key)
        )

    def create_issue(
        self, project_key: str, issue_type: str, summary: str, description: str, labels: list[str]
    ) -> Issue:
        fields = {
            "project": {"key": self._checked_project(project_key)},
            "issuetype": {"name": issue_type},
            "summary": summary,
            "description": description,
            "labels": labels,
        }
        if issue_type == "Epic":
            # Jira refuses an epic without its name; the summary is the natural one.
            fields |= {field: summary for field in self._epic_name_fields()}
        key = self._call(
            "issue",
            lambda data: str(data["key"]),
            body={"fields": fields},
            missing=ProjectNotFound(project_key),
        )
        # Built from Jira's answer instead of read back: once the task exists, a failed second
        # call must not report a failure and invite a second task. The status comes with the
        # sync (#16).
        return Issue(
            key=key,
            summary=summary,
            status="",
            issue_type=issue_type,
            project_key=project_key,
            done=False,
            url=f"{self.address}/browse/{key}",
        )

    def _epic_name_fields(self) -> list[str]:
        return self._call(
            "field",
            lambda data: [
                item["id"]
                for item in data
                if item.get("schema", {}).get("custom") == EPIC_NAME_TYPE
            ],
        )

    def _open_issue_by_key(self, project_key: str, key: str) -> list[Issue]:
        try:
            found = self.issue(key)
        except IssueNotFound:
            return []
        return [found] if found.project_key == project_key and not found.done else []

    def _checked_project(self, key: str) -> str:
        if not PROJECT_KEY.fullmatch(key):
            raise ProjectNotFound(key)
        return key

    def _issue(self, data: Any) -> Issue:
        fields = data["fields"]
        status = fields["status"]
        return Issue(
            key=data["key"],
            summary=fields["summary"],
            status=status["name"],
            issue_type=fields["issuetype"]["name"],
            project_key=fields["project"]["key"],
            done=status["statusCategory"]["key"] == "done",
            url=f"{self.address}/browse/{data['key']}",
        )

    def _call[T](
        self,
        path: str,
        parse: Callable[[Any], T],
        params: dict[str, str | int] | None = None,
        body: dict[str, Any] | None = None,
        missing: TrackerError | None = None,
    ) -> T:
        """Ask Jira and turn its answer into dimond's values with `parse`. Anything that is
        not the JSON Jira 8.13 sends (a proxy's login page, a changed field) becomes a
        readable TrackerUnreachable instead of a crash."""
        try:
            if body is None:
                response = self._client.get(path, params=params)
            else:
                response = self._client.post(path, json=body)
        except httpx.TransportError as error:
            raise TrackerUnreachable(f"Jira at {self.address} did not answer: {error}") from error
        self._raise_for_answer(response, path, missing)
        try:
            return parse(response.json())
        except ValueError as error:
            raise TrackerUnreachable(
                f"Jira at {self.address} answered {path} with something that is not JSON."
            ) from error
        except (KeyError, TypeError) as error:
            raise TrackerUnreachable(
                f"Jira at {self.address} answered {path} in an unexpected shape ({error!r})."
            ) from error

    def _raise_for_answer(
        self, response: httpx.Response, path: str, missing: TrackerError | None
    ) -> None:
        if response.is_redirect:
            raise TrackerUnreachable(
                f"Jira sent us to {response.headers.get('location')}; put that address in "
                "TRACKER_JIRA_URL."
            )
        if response.status_code == 401:
            raise TrackerSignInFailed(
                f"Jira did not accept the login {self.username!r}: check TRACKER_JIRA_USERNAME "
                "and TRACKER_JIRA_PASSWORD."
            )
        if response.status_code == 403:
            if "CAPTCHA" in response.headers.get("X-Authentication-Denied-Reason", ""):
                raise TrackerSignInFailed(
                    f"Jira asks {self.username!r} for a captcha after wrong passwords. Sign in "
                    "to Jira in a browser once as this user, then try again."
                )
            raise TrackerForbidden(f"{self.username!r} has no permission for this in Jira.")
        if response.status_code == 404 and missing is not None:
            raise missing
        if response.status_code == 400:
            raise TrackerRefused(f"Jira said no: {_jira_complaints(response)}")
        if response.is_error:
            raise TrackerUnreachable(f"Jira answered {response.status_code} to {path}.")


def _jira_complaints(response: httpx.Response) -> str:
    """Jira lists what it did not like as `errorMessages` and per-field `errors`."""
    try:
        data = response.json()
        complaints = [*data.get("errorMessages", []), *data.get("errors", {}).values()]
    except (ValueError, AttributeError):
        complaints = []
    return " ".join(str(c) for c in complaints) or f"it answered 400 to {response.url.path}."
