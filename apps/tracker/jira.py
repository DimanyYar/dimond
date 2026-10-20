"""Jira Server 8.13 through REST API v2 with Basic Auth (PRD: Jira sync; ADR 0008, 0017).

Personal access tokens arrived only in Jira 8.14, so the account's password goes with every
request. That is why the address must be https and redirects are never followed: Jira reports
its own base URL as http, and a redirect would send the password to wherever it points.
"""

import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

import httpx

from .base import (
    ISSUE_KEY,
    PROJECT_KEY,
    Account,
    Comment,
    Issue,
    IssueNotFound,
    Project,
    ProjectNotFound,
    Release,
    TaskFile,
    TrackerCaptcha,
    TrackerError,
    TrackerForbidden,
    TrackerNotConfigured,
    TrackerRefused,
    TrackerSignInFailed,
    TrackerUnreachable,
)

TIMEOUT_SECONDS = 15
OPEN_ISSUES_LIMIT = 20
# Jira 8.13 gives at most 50 tasks per search page unless its admin raised the limit.
LINKED_BATCH = 50
# Comments and files come with the search: Jira 8.13 returns every comment of every task in
# it (checked on j.shakuro.com, Oct 20, 2026: tasks with up to 36 comments came whole), so team
# questions cost no extra request.
ISSUE_FIELDS = "summary,status,issuetype,project,fixVersions,comment,attachment"
# Jira's text search treats these as operators and answers 400 to a stray one; a PM typing
# "form - duplicates?" means the words, not the operators.
TEXT_SEARCH_OPERATORS = re.compile(r"[^\w\s]")
# Jira Software keeps an epic's required short name in a custom field whose number differs
# from one Jira to another; its type is what identifies it.
EPIC_NAME_TYPE = "com.pyxis.greenhopper.jira:gh-epic-label"

# Jira's wiki markup in comment bodies, read into plain text for the client.
PICTURE = re.compile(r"!([^!\s|][^!|\n]*?)(?:\|[^!\n]*)?!")
FILE_LINK = re.compile(r"\[\^([^\]\n]+)\]")
MENTION = re.compile(r"\[~([^\]\n]+)\]")
LINK = re.compile(r"\[([^\]|\n]+)\|([^\]\n]+)\]")
BLOCK_MACRO = re.compile(r"\{(?:quote|code|noformat|panel|color)(?::[^}\n]*)?\}")
EXTRA_BLANK_LINES = re.compile(r"\n{3,}")


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

    def issues(self, keys: list[str]) -> list[Issue]:
        checked = [key for key in keys if ISSUE_KEY.fullmatch(key)]
        found: list[Issue] = []
        for start in range(0, len(checked), LINKED_BATCH):
            found += self._all_pages(f"key in ({','.join(checked[start : start + LINKED_BATCH])})")
        return found

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

    def attach_file(self, key: str, name: str, content: bytes, content_type: str) -> None:
        if not ISSUE_KEY.fullmatch(key):
            raise IssueNotFound(key)
        self._call(
            f"issue/{key}/attachments",
            lambda data: None,
            upload=(name, content, content_type),
            missing=IssueNotFound(key),
        )

    def add_comment(self, key: str, body: str) -> None:
        if not ISSUE_KEY.fullmatch(key):
            raise IssueNotFound(key)
        self._call(
            f"issue/{key}/comment",
            lambda data: None,
            body={"body": body},
            missing=IssueNotFound(key),
        )

    def file_content(self, file: TaskFile) -> bytes:
        try:
            response = self._client.get(
                f"{self.address}/secure/attachment/{quote(file.id)}/{quote(file.name)}"
            )
        except httpx.TransportError as error:
            raise TrackerUnreachable(f"Jira at {self.address} did not answer: {error}") from error
        self._raise_for_answer(response, "attachment", TrackerRefused(f"{file.name} is gone."))
        return response.content

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

    def _all_pages(self, jql: str) -> list[Issue]:
        """A Jira admin can cap search pages below what we ask for, so pages are read until
        Jira has given every task it counted."""
        found: list[Issue] = []
        while True:
            total, page = self._call(
                "search",
                lambda data: (int(data["total"]), [self._issue(item) for item in data["issues"]]),
                {
                    "jql": jql,
                    "fields": ISSUE_FIELDS,
                    "startAt": len(found),
                    "maxResults": LINKED_BATCH,
                    # With validation on, one deleted task's key makes Jira refuse the whole
                    # search; without it Jira just leaves that task out.
                    "validateQuery": "false",
                },
            )
            found += page
            if not page or len(found) >= total:
                return found

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
            releases=tuple(
                sorted(
                    (
                        Release(version["name"], date.fromisoformat(version["releaseDate"]))
                        for version in fields.get("fixVersions", [])
                        if version.get("releaseDate")
                    ),
                    key=lambda release: release.release_date,
                )
            ),
            comments=self._comments(fields),
        )

    def _comments(self, fields: Any) -> tuple[Comment, ...]:
        files = {
            item["filename"]: TaskFile(
                id=str(item["id"]),
                name=item["filename"],
                content_type=item.get("mimeType", ""),
                size=int(item["size"]),
            )
            for item in fields.get("attachment", [])
        }
        comments = []
        for item in fields.get("comment", {}).get("comments", []):
            text, shown = _plain_text(item["body"])
            author = item.get("author", {})
            login = author.get("name", "")
            comments.append(
                Comment(
                    id=str(item["id"]),
                    author_login=login,
                    author_name=author.get("displayName", login),
                    text=text,
                    created=datetime.fromisoformat(item["created"]),
                    updated=datetime.fromisoformat(item["updated"]),
                    files=tuple(files[name] for name in shown if name in files),
                    missing_files=tuple(name for name in shown if name not in files),
                    own=login.casefold() == self.username.casefold(),
                )
            )
        return tuple(comments)

    def _call[T](
        self,
        path: str,
        parse: Callable[[Any], T],
        params: dict[str, str | int] | None = None,
        body: dict[str, Any] | None = None,
        upload: tuple[str, bytes, str] | None = None,
        missing: TrackerError | None = None,
    ) -> T:
        """Ask Jira and turn its answer into dimond's values with `parse`. Anything that is
        not the JSON Jira 8.13 sends (a proxy's login page, a changed field) becomes a
        readable TrackerUnreachable instead of a crash."""
        try:
            if upload is not None:
                # Jira refuses uploads without this header, its guard against forged forms.
                response = self._client.post(
                    path, files={"file": upload}, headers={"X-Atlassian-Token": "no-check"}
                )
            elif body is not None:
                response = self._client.post(path, json=body)
            else:
                response = self._client.get(path, params=params)
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

    def _forbidden(self, response: httpx.Response) -> TrackerError:
        if "CAPTCHA" in response.headers.get("X-Authentication-Denied-Reason", ""):
            return TrackerCaptcha(
                f"Jira asks {self.username!r} for a captcha after wrong passwords. Sign in to "
                "Jira in a browser once as this user, then try again."
            )
        return TrackerForbidden(f"{self.username!r} has no permission for this in Jira.")

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
            raise self._forbidden(response)
        if response.status_code == 404 and missing is not None:
            raise missing
        if response.status_code == 400:
            raise TrackerRefused(f"Jira said no: {_jira_complaints(response)}")
        if response.status_code == 413:
            raise TrackerRefused("Jira said the file is larger than its attachment limit.")
        if response.is_error:
            raise TrackerUnreachable(f"Jira answered {response.status_code} to {path}.")


def _plain_text(body: str) -> tuple[str, list[str]]:
    """A comment as the client reads it, and the names of the files it shows, in order.
    Pictures and file links become the files beside the text; [~login] reads as @login, a
    link as its words and address."""
    shown: list[str] = []

    def file(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if name not in shown:
            shown.append(name)
        return ""

    text = body.replace("\r\n", "\n")
    text = PICTURE.sub(file, text)
    text = FILE_LINK.sub(file, text)
    text = MENTION.sub(lambda m: "@" + m.group(1).split("@")[0], text)
    text = LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)
    text = BLOCK_MACRO.sub("", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return EXTRA_BLANK_LINES.sub("\n\n", text).strip(), shown


def _jira_complaints(response: httpx.Response) -> str:
    """Jira lists what it did not like as `errorMessages` and per-field `errors`."""
    try:
        data = response.json()
        complaints = [*data.get("errorMessages", []), *data.get("errors", {}).values()]
    except (ValueError, AttributeError):
        complaints = []
    return " ".join(str(c) for c in complaints) or f"it answered 400 to {response.url.path}."
