import time
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.files.services import make_pending_previews


class Command(BaseCommand):
    help = (
        "Make previews for new attachments. With --every N, keep running and check every N seconds."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--every", type=int, help="Seconds between runs; omit to run once")

    def handle(self, *args: Any, every: int | None, **options: Any) -> None:
        while True:
            made = make_pending_previews()
            if made:
                self.stdout.write(f"Made {made} preview(s)")
            if every is None:
                return
            time.sleep(every)
