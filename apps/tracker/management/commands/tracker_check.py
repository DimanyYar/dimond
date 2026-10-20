from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.tracker.base import Comment, Issue, TrackerError
from apps.tracker.connection import current_tracker


class Command(BaseCommand):
    help = (
        "Check the tracker chosen in the admin: who dimond signs in as, which projects it sees, "
        "and optionally one project's issue types and open tasks, or one task with its comments "
        "and which of them are questions for the client (#client). Only reads, changes nothing."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--project", help="A project key, e.g. FI")
        parser.add_argument("--find", default="", help="With --project: a task key or words")
        parser.add_argument("--issue", help="One task key, e.g. FI-2190")

    def handle(
        self, *args: Any, project: str | None, find: str, issue: str | None, **options: Any
    ) -> None:
        try:
            self._check(project, find, issue)
        except TrackerError as error:
            raise CommandError(str(error)) from error

    def _check(self, project: str | None, find: str, issue: str | None) -> None:
        tracker = current_tracker()
        account = tracker.account()
        self.stdout.write(f"Tracker: {tracker.name} at {tracker.address}")
        self.stdout.write(f"Signed in as {account.display_name} ({account.login})")
        projects = tracker.projects()
        self.stdout.write(f"Projects this account sees ({len(projects)}):")
        for item in projects:
            self.stdout.write(f"  {item.key} · {item.name}")
        if project:
            self.stdout.write(
                f"Issue types in {project}: {', '.join(tracker.issue_types(project))}"
            )
            found = tracker.open_issues(project, find)
            self.stdout.write(f"Open tasks in {project}{f' matching {find!r}' if find else ''}:")
            for task in found:
                self.stdout.write(
                    f"  {task.key}  {task.status} · {task.issue_type} · {task.summary}"
                )
            if not found:
                self.stdout.write("  none")
        if issue:
            self._task(tracker.issue(issue))

    def _task(self, task: Issue) -> None:
        self.stdout.write(
            f"{task.key}: {task.summary}\n  {task.issue_type} · {task.status}"
            f"{' (done)' if task.done else ''} · project {task.project_key}\n  {task.url}"
        )
        self.stdout.write(f"Comments ({len(task.comments)}):")
        for comment in task.comments:
            self._comment(comment)

    def _comment(self, comment: Comment) -> None:
        kind = "question for the client" if comment.asks_the_client else "team only"
        if comment.own:
            kind = "written by dimond's account"
        self.stdout.write(
            f"  #{comment.id} · {comment.author_name} · {comment.created:%Y-%m-%d %H:%M} · {kind}"
        )
        self.stdout.write("    " + comment.text.replace("\n", "\n    "))
        for file in comment.files:
            self.stdout.write(f"    file: {file.name} ({file.size} bytes)")
        for name in comment.missing_files:
            self.stdout.write(f"    file not found on the task: {name}")
