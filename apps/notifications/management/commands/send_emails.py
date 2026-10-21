import time
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.accounts.services import send_queued_links
from apps.notifications.services import send_request_updates


class Command(BaseCommand):
    help = "Send queued emails. With --every N, keep running and check every N seconds."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--every", type=int, help="Seconds between runs; omit to run once")

    def handle(self, *args: Any, every: int | None, **options: Any) -> None:
        while True:
            # Sign-in links first: someone is waiting at the sign-in page for them.
            sent = send_queued_links() + send_request_updates()
            if sent:
                self.stdout.write(f"Sent {sent} email(s)")
            if every is None:
                return
            time.sleep(every)
