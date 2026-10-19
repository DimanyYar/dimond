"""Jira Server 8.13 through REST API v2 with Basic Auth (PRD: Jira sync; ADR 0008, 0017).

Personal access tokens arrived only in Jira 8.14, so the account's password goes with every
request. That is why the address must be https and redirects are never followed: Jira reports
its own base URL as http, and a redirect would send the password to wherever it points.
"""

import re
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
    TrackerSignInFailed,
    TrackerUnreachable,
)

TIMEOUT_SECONDS = 15
OPEN_ISSUES_LIMIT = 20
ISSUE_FIELDS = "summary,status,issuetype,project"
# Jira's text search treats these as operators and answers 400 to a stray one; a PM typing
# "form - duplicates?" means the words, not the operators.
TEXT_SEARCH_OPERATORS = re.compile(r"[^\w\s]")


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
        data = self._get("myself")
        return Account(login=data["name"], display_name=data["displayName"])

    def projects(self) -> list[Project]:
        data = self._get("project")
        return sorted(
            (Project(key=item["key"], name=item["name"]) for item in data), key=lambda p: p.key
        )

    def issue_types(self, project_key: str) -> list[str]:
        data = self._get(
            f"project/{self._checked_project(project_key)}", missing=ProjectNotFound(project_key)
        )
        return [item["name"] for item in data["issueTypes"] if not item["subtask"]]

    def open_issues(self, project_key: str, text: str = "") -> list[Issue]:
        project_key = self._checked_project(project_key)
        text = text.strip()
        if ISSUE_KEY.fullmatch(text.upper()):
            return self._open_issue_by_key(project_key, text.upper())
        jql = f'project = "{project_key}" AND statusCategory != Done'
        words = TEXT_SEARCH_OPERATORS.sub(" ", text).strip()
        if words:
            jql += f' AND summary ~ "{words}"'
        data = self._get(
            "search",
            {
                "jql": f"{jql} ORDER BY created DESC",
                "fields": ISSUE_FIELDS,
                "maxResults": OPEN_ISSUES_LIMIT,
            },
            missing=ProjectNotFound(project_key),
        )
        return [self._issue(item) for item in data["issues"]]

    def issue(self, key: str) -> Issue:
        if not ISSUE_KEY.fullmatch(key):
            raise IssueNotFound(key)
        data = self._get(f"issue/{key}", {"fields": ISSUE_FIELDS}, missing=IssueNotFound(key))
        return self._issue(data)

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

    def _get(
        self,
        path: str,
        params: dict[str, str | int] | None = None,
        missing: TrackerError | None = None,
    ) -> Any:
        try:
            response = self._client.get(path, params=params)
        except httpx.TransportError as error:
            raise TrackerUnreachable(f"Jira at {self.address} did not answer: {error}") from error
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
        if response.is_error:
            raise TrackerUnreachable(f"Jira answered {response.status_code} to {path}.")
        return response.json()
