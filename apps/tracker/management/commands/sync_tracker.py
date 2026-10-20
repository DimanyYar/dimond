import time
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.tracker.services import sync_tasks


class Command(BaseCommand):
    help = (
        "Bring Jira task statuses back to their requests. With --every N, keep running and sync "
        "every N seconds."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--every", type=int, help="Seconds between runs; omit to run once")

    def handle(self, *args: Any, every: int | None, **options: Any) -> None:
        while True:
            run = sync_tasks()
            changes = run.events.count()
            line = f"{run.get_outcome_display()}: {run.checked} task(s), {changes} change(s)"
            self.stdout.write(f"{line}. {run.error}" if run.error else line)
            if every is None:
                return
            time.sleep(every)
