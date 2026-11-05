"""The demo tracker: fixed tasks in memory, no network (ADR 0008).

Tests, local development and the public demo use it, so it holds the same Atlas story as the
mockups.
"""

import base64
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import count

from .base import (
    Account,
    Comment,
    Issue,
    IssueNotFound,
    Project,
    ProjectNotFound,
    Release,
    ReleaseTask,
    Sprint,
    TaskFile,
    TaskSprint,
    TrackerRefused,
    Version,
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


# Atlas sorts tasks into sprints it never closes, as Dmitry does; Harbor Foods has two boards;
# Northwind has none, so its tasks can only go to the backlog.
SPRINTS = {
    "FI": [
        Sprint(
            101,
            "Sprint 12",
            "FI board",
            board_id=88,
            state="active",
            starts=datetime(2026, 10, 20, tzinfo=UTC),
            ends=datetime(2026, 11, 10, tzinfo=UTC),
        ),
        Sprint(102, "Bugs bucket", "FI board", board_id=88),
        Sprint(103, "Ideas bucket", "FI board", board_id=88),
        Sprint(104, "Old bucket", "FI board", board_id=88),
    ],
    "HF": [
        Sprint(201, "Web bucket", "HF web", board_id=91),
        Sprint(202, "Mobile bucket", "HF mobile", board_id=92),
    ],
}
# Plays a sprint someone closed after the PM opened the window: still listed, but Jira refuses
# to put a task in it.
CLOSING_SPRINT = 104


# Atlas's releases as Jira FI keeps them (canvas 7, "What Jira FI really looks like"): open
# versions have no date yet, 25.8.0 was never released, Hot Fix and Unreleased are buckets.
VERSIONS = {
    "FI": [
        Version("20090", "25.1.0", None, date(2025, 3, 4), released=True, archived=True),
        Version("20100", "25.8.0", None, None, released=False, archived=False),
        Version("20110", "26.6.0", None, date(2026, 8, 26), released=True, archived=False),
        Version("20120", "26.7.0", None, date(2026, 9, 30), released=True, archived=False),
        Version("20130", "26.8.0", None, None, released=False, archived=False),
        Version("20135", "26.8.5", None, None, released=False, archived=False),
        Version("20140", "26.9.0", None, None, released=False, archived=False),
        Version("20150", "Hot Fix", None, None, released=False, archived=False),
        Version("20160", "Unreleased", None, None, released=False, archived=False),
    ]
}
SPRINT_11 = TaskSprint(
    100,
    "Sprint 11",
    "closed",
    datetime(2026, 9, 29, tzinfo=UTC),
    datetime(2026, 10, 20, tzinfo=UTC),
    board_id=88,
)
SPRINT_12 = TaskSprint(
    101,
    "Sprint 12",
    "active",
    datetime(2026, 10, 20, tzinfo=UTC),
    datetime(2026, 11, 10, tzinfo=UTC),
    board_id=88,
)
OLGA, IVAN, ANNA = "Olga Petrova", "Ivan Kuznetsov", "Anna Sokolova"
# Key, title, type, status, then the fix version's id for a task (empty: a sprint's task with no
# release yet) or the parent's key for a subtask, and who it is assigned to. Stories stay
# unassigned in To Do while subtasks move.
RELEASE_ROWS = [
    ("FI-2214", "Filter the leads list by source", "Story", "To Do", "20130", ""),
    ("FI-2241", "[BE] Source filter in the leads API", "Sub-task", "Done", "FI-2214", OLGA),
    (
        "FI-2242",
        "[FE] Source dropdown on the leads list",
        "Sub-task",
        "Code review",
        "FI-2214",
        IVAN,
    ),
    ("FI-2243", "[QA] Test the source filter", "Sub-task", "To Do", "FI-2214", ANNA),
    ("FI-2244", "Archived sources are still listed", "Sub-bug", "In Progress", "FI-2214", IVAN),
    ("FI-2208", "Lead form: validate phone numbers", "Story", "To Do", "20130", ""),
    ("FI-2245", "[BE] Phone number rules", "Sub-task", "Done", "FI-2208", OLGA),
    ("FI-2246", "[FE] Phone field hints", "Sub-task", "In Progress", "FI-2208", IVAN),
    ("FI-2231", "Web form: leads created twice on submit", "Bug", "To Do", "20130", OLGA),
    ("FI-2197", "Export leads to CSV in the background", "Story", "To Do", "20140", ""),
    ("FI-2248", "[BE] Background export job", "Sub-task", "To Do", "FI-2197", ""),
    ("FI-2120", "Remove the old lead widget", "Task", "Done", "20100", IVAN),
    ("FI-2150", "Leads list: sort by date", "Story", "Done", "20120", ""),
    ("FI-2151", "[FE] Sort control", "Sub-task", "Done", "FI-2150", IVAN),
    ("FI-2101", "Faster leads search", "Task", "Done", "20110", OLGA),
    ("FI-2090", "Sign-in link expired too early", "Bug", "Done", "20150", OLGA),
    ("FI-2060", "CSV export timed out", "Bug", "Done", "20150", IVAN),
    ("FI-2010", "Lead scoring ideas", "Story", "To Do", "20160", ""),
    ("FI-2260", "Remove unused lead fields", "Task", "In Progress", "", OLGA),
    ("FI-2261", "[BE] Drop the old columns", "Sub-task", "In Progress", "FI-2260", OLGA),
]
RELEASE_SPRINTS = {
    "FI-2214": (SPRINT_12,),
    "FI-2208": (SPRINT_11, SPRINT_12),
    "FI-2260": (SPRINT_12,),
}
# Hot Fix shows what was closed lately, so one demo fix stays recent whenever the demo runs.
RESOLVED = {
    "FI-2150": datetime(2026, 9, 29, tzinfo=UTC),
    "FI-2090": datetime.now(UTC) - timedelta(days=3),
    "FI-2060": datetime.now(UTC) - timedelta(days=45),
}


def _release_task(
    key: str, summary: str, issue_type: str, status: str, version_or_parent: str, who: str
) -> ReleaseTask:
    parent = version_or_parent if "-" in version_or_parent else ""
    return ReleaseTask(
        key=key,
        summary=summary,
        issue_type=issue_type,
        subtask=bool(parent),
        parent_key=parent,
        status=status,
        status_category={"To Do": "new", "Done": "done"}.get(status, "indeterminate"),
        assignee_login=who.split()[0].lower() if who else "",
        assignee_name=who,
        version_ids=() if parent or not version_or_parent else (version_or_parent,),
        sprints=RELEASE_SPRINTS.get(key, ()),
        resolved=RESOLVED.get(key),
    )


RELEASE_TASKS = [_release_task(*row) for row in RELEASE_ROWS]


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

    def open_sprints(self, project_key: str) -> list[Sprint]:
        self._check_project(project_key)
        return list(SPRINTS.get(project_key, []))

    def move_to_sprint(self, key: str, sprint_id: int) -> None:
        self.issue(key)
        if sprint_id == CLOSING_SPRINT:
            raise TrackerRefused("Jira said no: Issues can't be moved to a closed sprint.")

    def attach_file(self, key: str, name: str, content: bytes, content_type: str) -> None:
        """The demo keeps no files: a known key is enough to say it went well."""
        if not self._has_project(key.split("-")[0]):
            raise IssueNotFound(key)

    def add_comment(self, key: str, body: str) -> str:
        """The demo keeps no comments: a known task is enough to say it went well."""
        self.issue(key)
        return str(next(_new_numbers))

    def versions(self, project_key: str) -> list[Version]:
        self._check_project(project_key)
        return list(VERSIONS.get(project_key, []))

    def release_tasks(
        self,
        project_key: str,
        version_ids: list[str],
        recent_ids: list[str],
        closed_since: date,
        sprint_ids: Sequence[int] = (),
    ) -> list[ReleaseTask]:
        self._check_project(project_key)

        def wanted(task: ReleaseTask) -> bool:
            if task.subtask or not task.key.startswith(f"{project_key}-"):
                return False
            if set(task.version_ids) & set(version_ids):
                return True
            if {s.id for s in task.sprints} & set(sprint_ids):
                return True
            recent = task.resolved is not None and task.resolved.date() >= closed_since
            return recent and bool(set(task.version_ids) & set(recent_ids))

        parents = {task.key for task in RELEASE_TASKS if wanted(task)}
        return [t for t in RELEASE_TASKS if t.key in parents or t.parent_key in parents]

    def file_content(self, file: TaskFile) -> bytes:
        if file.id not in FILES:
            raise TrackerRefused(f"{file.name} is gone.")
        return FILES[file.id]

    def _check_project(self, key: str) -> None:
        if not self._has_project(key):
            raise ProjectNotFound(key)

    def _has_project(self, key: str) -> bool:
        return key in {project.key for project in PROJECTS}
