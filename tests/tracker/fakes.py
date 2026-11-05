from collections.abc import Sequence
from datetime import date

from apps.tracker.base import ReleaseTask, Sprint, Version


class NoReleases:
    """For fake trackers in tests about something else: every sync run also copies releases
    and the tasks of open sprints (ADR 0026), and these projects have none."""

    def versions(self, project_key: str) -> list[Version]:
        return []

    def open_sprints(self, project_key: str) -> list[Sprint]:
        return []

    def release_tasks(
        self,
        project_key: str,
        version_ids: list[str],
        recent_ids: list[str],
        closed_since: date,
        sprint_ids: Sequence[int] = (),
    ) -> list[ReleaseTask]:
        return []
