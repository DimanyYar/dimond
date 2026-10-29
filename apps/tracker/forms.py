"""The sprint list shared by the client's default sprint and the Create task window (PRD: Jira
sync, "Create task"; roadmap #29)."""

from dataclasses import dataclass, field

from django import forms

from .base import Sprint, TrackerError
from .connection import current_tracker

BACKLOG = ""
SPRINT_ERRORS = {
    "invalid_choice": "This sprint is closed or gone from Jira. Pick another one, or Backlog."
}


@dataclass
class SprintList:
    sprints: dict[str, Sprint] = field(default_factory=dict)
    # Why the list holds only Backlog when Jira did not give the sprints.
    error: str = ""
    # The client's default sprint when Jira no longer lists it as open.
    gone: str = ""

    def chosen(self, value: str) -> Sprint | None:
        return self.sprints.get(value)


def sprint_field() -> forms.ChoiceField:
    return forms.ChoiceField(required=False, error_messages=SPRINT_ERRORS)


def fill_sprints(
    choice: forms.ChoiceField, project_key: str, default_id: int | None, default_name: str
) -> SprintList:
    """Backlog and the project's open sprints, the client's default chosen. The list is asked
    for again when a form is sent, so a sprint closed in between is refused there. Jira not
    answering never blocks a task: Backlog is always there."""
    found = SprintList()
    try:
        sprints = current_tracker().open_sprints(project_key)
    except TrackerError as problem:
        sprints = []
        found.error = f"Couldn't load the sprints: {problem}"
    boards = {sprint.board for sprint in sprints}
    found.sprints = {str(sprint.id): sprint for sprint in sprints}
    choice.choices = [(BACKLOG, "Backlog")] + [
        (value, f"{sprint.board} · {sprint.name}" if len(boards) > 1 else sprint.name)
        for value, sprint in found.sprints.items()
    ]
    default = str(default_id) if default_id is not None else BACKLOG
    if default in found.sprints or default == BACKLOG:
        choice.initial = default
    else:
        choice.initial = BACKLOG
        # Only Jira's own answer says a sprint is gone; a Jira that did not answer says nothing.
        if not found.error:
            found.gone = default_name
    return found
