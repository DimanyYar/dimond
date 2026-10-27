"""What every tracker adapter gives the rest of dimond (ADR 0008).

Nothing outside `apps.tracker` talks to Jira directly: it asks `current_tracker()` for an
object with these methods and gets plain values back.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

# A project key as Jira allows it: FI, ATL, HR2. Checked before a key goes into a query.
PROJECT_KEY = re.compile(r"[A-Z][A-Z0-9_]+")
ISSUE_KEY = re.compile(r"[A-Z][A-Z0-9_]+-\d+")
# The team marks a comment meant for the client with this tag, in plain text: a mention of a
# user would need a service account, and while the sync signs in as Dmitry every mention of him
# would reach the client (PRD: Jira sync, team questions).
# Not after "/": that is a link's address ending in #client, not the tag. Not after "\":
# dimond escapes the tag that way when a client types it in an answer.
CLIENT_TAG = re.compile(r"(?<![\w#/\\])#client(?![\w-])", re.IGNORECASE)


def tags_the_client(text: str) -> bool:
    """Text outside quotes only: dimond's answers quote the question, tag included, and are
    written by the same account as Dmitry's own questions, so the author cannot tell them
    apart."""
    return CLIENT_TAG.search(text) is not None


@dataclass(frozen=True)
class Account:
    login: str
    display_name: str


@dataclass(frozen=True)
class Project:
    key: str
    name: str


@dataclass(frozen=True)
class Release:
    """A fix version of a task that has a release date; versions without one tell the client
    nothing yet, so the adapters leave them out."""

    name: str
    release_date: date


@dataclass(frozen=True)
class TaskFile:
    """A file attached to a task. Comments show a task's files by name."""

    id: str
    name: str
    content_type: str
    size: int


@dataclass(frozen=True)
class Comment:
    id: str
    author_login: str
    author_name: str
    # Plain text: the tracker's own markup is gone, people mentioned read as @name.
    text: str
    created: datetime
    updated: datetime
    # The task's files the comment shows or links to.
    files: tuple[TaskFile, ...] = ()
    # Pictures or files it shows that the task no longer has, or that live elsewhere.
    missing_files: tuple[str, ...] = ()
    # A question from the team for the client: the client tag outside quotes.
    asks_the_client: bool = False
    # The comment as the team wrote it, in the tracker's markup, for the thread to format.
    markup: str = ""


@dataclass(frozen=True)
class Issue:
    key: str
    summary: str
    status: str
    issue_type: str
    project_key: str
    done: bool
    url: str
    # Soonest first.
    releases: tuple[Release, ...] = ()
    # Oldest first.
    comments: tuple[Comment, ...] = ()


class TrackerError(Exception):
    """A problem the PM can read and act on; the message says what happened in plain words."""


class TrackerNotConfigured(TrackerError):
    pass


class TrackerUnreachable(TrackerError):
    pass


class TrackerSignInFailed(TrackerError):
    pass


class TrackerCaptcha(TrackerSignInFailed):
    """Too many wrong passwords: Jira wants a captcha, which only a person in a browser can
    solve. The password may be right by now."""


class TrackerForbidden(TrackerError):
    pass


class TrackerRefused(TrackerError):
    """The tracker read the request and said no, e.g. a field the project requires."""


class ProjectNotFound(TrackerError):
    def __init__(self, key: str) -> None:
        super().__init__(f"There is no project {key}, or the tracker account cannot see it.")


class IssueNotFound(TrackerError):
    def __init__(self, key: str) -> None:
        super().__init__(f"There is no task {key}, or the tracker account cannot see it.")


class Tracker(Protocol):
    name: str
    address: str

    def account(self) -> Account: ...

    def projects(self) -> list[Project]: ...

    def issue_types(self, project_key: str) -> list[str]: ...

    def open_issues(self, project_key: str, text: str = "") -> list[Issue]:
        """Tasks of the project that are not done, newest first. `text` is a task key or
        words from the title."""
        ...

    def issue(self, key: str) -> Issue: ...

    def issues(self, keys: list[str]) -> list[Issue]:
        """The tasks behind these keys as they are now, for the status sync. A deleted task,
        or one the account cannot see, is left out; a task moved to another project comes
        back under its new key."""
        ...

    def create_issue(
        self, project_key: str, issue_type: str, summary: str, description: str, labels: list[str]
    ) -> Issue: ...

    def attach_file(self, key: str, name: str, content: bytes, content_type: str) -> None: ...

    def add_comment(self, key: str, body: str) -> str:
        """Adds a comment in the tracker's markup and returns its id."""
        ...

    def file_content(self, file: TaskFile) -> bytes:
        """What a task's file holds, e.g. a screenshot in a team question. Callers check
        `file.size` first: this reads the whole file into memory."""
        ...
