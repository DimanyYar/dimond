"""The demo tracker: fixed tasks in memory, no network (ADR 0008).

Tests, local development and the public demo use it, so it holds the same Atlas story as the
mockups.
"""

import time
from itertools import count

from .base import Account, Issue, IssueNotFound, Project, ProjectNotFound, TrackerRefused

DEMO_ADDRESS = "https://jira.example.com"
PROJECTS = [Project("FI", "Atlas"), Project("HF", "Harbor Foods"), Project("NW", "Northwind")]
ISSUE_TYPES = ["Bug", "Story", "Task"]


def _issue(key: str, summary: str, status: str, issue_type: str, done: bool = False) -> Issue:
    return Issue(
        key=key,
        summary=summary,
        status=status,
        issue_type=issue_type,
        project_key=key.split("-")[0],
        done=done,
        url=f"{DEMO_ADDRESS}/browse/{key}",
    )


# Newest first, as Jira lists them.
ISSUES = [
    _issue("FI-2231", "Web form: leads created twice on submit", "To Do", "Bug"),
    _issue("FI-2214", "Filter the leads list by source", "Code review", "Story"),
    _issue("FI-2208", "Lead form: validate phone numbers", "In Progress", "Story"),
    _issue("FI-2197", "Export leads to CSV in the background", "To Do", "Story"),
    _issue("FI-2190", "Lead card: show the source campaign", "on Staging", "Story"),
    _issue("FI-2150", "Leads list: sort by date", "Done", "Story", done=True),
    _issue("NW-14", "Invoice PDF shows the wrong VAT", "To Do", "Bug"),
]


# New demo tasks are not stored, but their keys are saved on requests, which must stay unique
# across restarts: numbering starts from the second the process started, so a restart begins
# above every key the previous run gave out.
_new_numbers = count(int(time.time()) % 1_000_000 + 10_000)


class MockTracker:
    name = "Demo"
    address = DEMO_ADDRESS

    def account(self) -> Account:
        return Account(login="demo", display_name="Demo tracker")

    def projects(self) -> list[Project]:
        return list(PROJECTS)

    def issue_types(self, project_key: str) -> list[str]:
        self._check_project(project_key)
        return list(ISSUE_TYPES)

    def open_issues(self, project_key: str, text: str = "") -> list[Issue]:
        self._check_project(project_key)
        text = text.strip().lower()
        return [
            issue
            for issue in ISSUES
            if issue.project_key == project_key
            and not issue.done
            and (not text or text == issue.key.lower() or text in issue.summary.lower())
        ]

    def issue(self, key: str) -> Issue:
        for issue in ISSUES:
            if issue.key == key:
                return issue
        raise IssueNotFound(key)

    def create_issue(
        self, project_key: str, issue_type: str, summary: str, description: str, labels: list[str]
    ) -> Issue:
        self._check_project(project_key)
        if issue_type not in ISSUE_TYPES:
            raise TrackerRefused(f"The demo tracker has no issue type {issue_type!r}.")
        return _issue(f"{project_key}-{next(_new_numbers)}", summary, "To Do", issue_type)

    def attach_file(self, key: str, name: str, content: bytes, content_type: str) -> None:
        """The demo keeps no files: a known key is enough to say it went well."""
        if key.split("-")[0] not in {project.key for project in PROJECTS}:
            raise IssueNotFound(key)

    def add_comment(self, key: str, body: str) -> None:
        """The demo keeps no comments: a known task is enough to say it went well."""
        self.issue(key)

    def _check_project(self, key: str) -> None:
        if key not in {project.key for project in PROJECTS}:
            raise ProjectNotFound(key)
