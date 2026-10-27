from collections.abc import Callable
from zoneinfo import ZoneInfo

from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from .models import User
from .selectors import has_account_page


class PersonTimeZone:
    """Shows every time on a page in the signed-in person's own zone (PRD: account). Times
    are stored in UTC; templates convert them at the edge, as CLAUDE.md asks. No zone picked
    yet, nobody signed in, or the team, who cannot pick one (ADR 0023): UTC."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = request.user
        # The team keeps UTC even with a zone saved, e.g. from before a role change in the admin.
        zone = None
        if isinstance(user, User) and has_account_page(user) and user.time_zone:
            zone = ZoneInfo(user.time_zone)
        # override() puts the previous zone back when the page is done, errors included, so
        # nothing that runs after it in the same thread inherits this person's zone.
        with timezone.override(zone):
            return self.get_response(request)
