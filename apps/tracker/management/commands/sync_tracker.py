import time
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.tracker.models import SyncRun
from apps.tracker.services import (
    QUEUE_CHECK_SECONDS,
    close_cut_off_runs,
    next_queued_run,
    seconds_until_next,
    sync_paused,
    sync_tasks,
)


class Command(BaseCommand):
    help = (
        "Bring Jira task statuses back to their requests. With --every N, keep running: sync "
        "every N seconds, and at once when a PM presses Sync now or a Jira webhook arrives."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--every", type=int, help="Seconds between runs; omit to run once")

    def handle(self, *args: Any, every: int | None, **options: Any) -> None:
        if every is None:
            self._report(sync_tasks(next_queued_run()))
            return
        # Only one worker runs, so anything still "running" now is from before it restarted.
        close_cut_off_runs()
        due, retried = 0.0, False
        while True:
            queued = next_queued_run()
            if queued is None and time.monotonic() < due:
                pass
            elif queued is None and sync_paused():
                due, retried = time.monotonic() + every, False
            else:
                # A queued run counts as the scheduled one too: the next one waits its turn.
                run = sync_tasks(queued)
                self._report(run)
                wait, retried = seconds_until_next(run, every, retried)
                due = time.monotonic() + wait
            time.sleep(QUEUE_CHECK_SECONDS)

    def _report(self, run: SyncRun) -> None:
        changes = run.events.count()
        line = f"{run.get_outcome_display()}: {run.checked} task(s), {changes} change(s)"
        self.stdout.write(f"{line}. {run.error}" if run.error else line)
