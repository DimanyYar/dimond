"""The demo tracker: fixed tasks in memory, no network (ADR 0008).

Tests, local development and the public demo use it, so it holds the same Atlas story as the
mockups.
"""

import base64
import time
from datetime import UTC, date, datetime
from itertools import count

from .base import (
    Account,
    Comment,
    Issue,
    IssueNotFound,
    Project,
    ProjectNotFound,
    Release,
    TaskFile,
    TrackerRefused,
)
from .jira_markup import to_text

DEMO_ADDRESS = "https://jira.example.com"
PROJECTS = [Project("FI", "Atlas"), Project("HF", "Harbor Foods"), Project("NW", "Northwind")]
ISSUE_TYPES = ["Bug", "Story", "Task"]


def _issue(
    key: str,
    summary: str,
    status: str,
    issue_type: str,
    done: bool = False,
    releases: tuple[Release, ...] = (),
    comments: tuple[Comment, ...] = (),
) -> Issue:
    return Issue(
        key=key,
        summary=summary,
        status=status,
        issue_type=issue_type,
        project_key=key.split("-")[0],
        done=done,
        url=f"{DEMO_ADDRESS}/browse/{key}",
        releases=releases,
        comments=comments,
    )


# A one-pixel PNG: the demo's screenshot, small but a real picture for the upload checks.
DEMO_PICTURE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
)
SOURCES_SCREENSHOT = TaskFile("90001", "lead-sources.png", "image/png", len(DEMO_PICTURE))
FILES = {SOURCES_SCREENSHOT.id: DEMO_PICTURE}

QUESTION_MARKUP = (
    "#client Which sources should the list show: *only the ones you use now*, or every source "
    "from [CRM settings|https://atlas.example.com/settings/sources], including _archived_ ones?"
)
# The team question from the mockups (RequestCardTeamAsk) and an internal note that must never
# reach the client.
FI_2214_COMMENTS = (
    Comment(
        id="70001",
        author_login="olga.p",
        author_name="Olga Petrova",
        text="@dsmirnov archived sources are still in the settings table, I can hide them.",
        created=datetime(2026, 10, 13, 9, 5, tzinfo=UTC),
        updated=datetime(2026, 10, 13, 9, 5, tzinfo=UTC),
    ),
    Comment(
        id="70002",
        author_login="olga.p",
        author_name="Olga Petrova",
        text=to_text(QUESTION_MARKUP),
        created=datetime(2026, 10, 20, 8, 44, tzinfo=UTC),
        updated=datetime(2026, 10, 20, 8, 44, tzinfo=UTC),
        files=(SOURCES_SCREENSHOT,),
        asks_the_client=True,
        markup=QUESTION_MARKUP,
    ),
)


# Newest first, as Jira lists them.
ISSUES = [
    _issue("FI-2231", "Web form: leads created twice on submit", "To Do", "Bug"),
    _issue(
        "FI-2214",
        "Filter the leads list by source",
        "Code review",
        "Story",
        comments=FI_2214_COMMENTS,
    ),
    _issue("FI-2208", "Lead form: validate phone numbers", "In Progress", "Story"),
    _issue("FI-2197", "Export leads to CSV in the background", "To Do", "Story"),
    _issue(
        "FI-2190",
        "Lead card: show the source campaign",
        "on Staging",
        "Story",
        releases=(Release("2.15", date(2026, 10, 28)),),
    ),
    _issue("FI-2150", "Leads list: sort by date", "Done", "Story", done=True),
    _issue("NW-14", "Invoice PDF shows the wrong VAT", "To Do", "Bug"),
]


# New demo tasks are not stored, but their keys are saved on requests, which must stay unique
# across restarts: numbering starts from the second the process started, so a restart begins
# above every key the previous run gave out.
# Demo tasks in ISSUES are numbered below this, so a higher number is always one the demo made.
DEMO_MADE_FROM = 10_000
_new_numbers = count(int(time.time()) % 1_000_000 + DEMO_MADE_FROM)


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
        # Tasks the demo made are not stored (the sync runs in another process anyway), so
        # their number is what vouches for them; they stay in To Do for good.
        project_key, _, number = key.partition("-")
        # isdigit() alone lets "²" through, which int() then refuses.
        is_number = number.isascii() and number.isdecimal()
        if self._has_project(project_key) and is_number and int(number) >= DEMO_MADE_FROM:
            return _issue(key, "A task made in the demo", "To Do", "Task")
        raise IssueNotFound(key)

    def issues(self, keys: list[str]) -> list[Issue]:
        found = []
        for key in keys:
            try:
                found.append(self.issue(key))
            except IssueNotFound:
                continue
        return found

    def create_issue(
        self, project_key: str, issue_type: str, summary: str, description: str, labels: list[str]
    ) -> Issue:
        self._check_project(project_key)
        if issue_type not in ISSUE_TYPES:
            raise TrackerRefused(f"The demo tracker has no issue type {issue_type!r}.")
        return _issue(f"{project_key}-{next(_new_numbers)}", summary, "To Do", issue_type)

    def attach_file(self, key: str, name: str, content: bytes, content_type: str) -> None:
        """The demo keeps no files: a known key is enough to say it went well."""
        if not self._has_project(key.split("-")[0]):
            raise IssueNotFound(key)

    def add_comment(self, key: str, body: str) -> str:
        """The demo keeps no comments: a known task is enough to say it went well."""
        self.issue(key)
        return str(next(_new_numbers))

    def file_content(self, file: TaskFile) -> bytes:
        if file.id not in FILES:
            raise TrackerRefused(f"{file.name} is gone.")
        return FILES[file.id]

    def _check_project(self, key: str) -> None:
        if not self._has_project(key):
            raise ProjectNotFound(key)

    def _has_project(self, key: str) -> bool:
        return key in {project.key for project in PROJECTS}
