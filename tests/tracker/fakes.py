from datetime import date

from apps.tracker.base import ReleaseTask, Version


class NoReleases:
    """For fake trackers in tests about something else: every sync run also copies releases
    (ADR 0026), and these projects have none."""

    def versions(self, project_key: str) -> list[Version]:
        return []

    def release_tasks(
        self, project_key: str, version_ids: list[str], recent_ids: list[str], closed_since: date
    ) -> list[ReleaseTask]:
        return []
