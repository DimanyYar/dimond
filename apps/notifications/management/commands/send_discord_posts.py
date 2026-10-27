import time
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.notifications.services import send_discord_posts


class Command(BaseCommand):
    help = "Send waiting Discord posts. With --every N, keep running and check every N seconds."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--every", type=int, help="Seconds between runs; omit to run once")

    def handle(self, *args: Any, every: int | None, **options: Any) -> None:
        while True:
            sent = send_discord_posts()
            if sent:
                self.stdout.write(f"Posted {sent} topic(s) to Discord")
            if every is None:
                return
            time.sleep(every)
