"""What every tracker adapter gives the rest of dimond (ADR 0008).

Nothing outside `apps.tracker` talks to Jira directly: it asks `current_tracker()` for an
object with these methods and gets plain values back.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol

# A project key as Jira allows it: FI, ATL, HR2. Checked before a key goes into a query.
PROJECT_KEY = re.compile(r"[A-Z][A-Z0-9_]+")
ISSUE_KEY = re.compile(r"[A-Z][A-Z0-9_]+-\d+")


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

    def add_comment(self, key: str, body: str) -> None: ...
