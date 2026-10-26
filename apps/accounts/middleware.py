from collections.abc import Callable
from zoneinfo import ZoneInfo

from django.http import HttpRequest, HttpResponse
from django.utils import timezone


class PersonTimeZone:
    """Shows every time on a page in the signed-in person's own zone (PRD: account). Times
    are stored in UTC; templates convert them at the edge, as CLAUDE.md asks. No zone picked
    yet, or nobody signed in: UTC."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        zone = getattr(request.user, "time_zone", "")
        if zone:
            timezone.activate(ZoneInfo(zone))
        else:
            timezone.deactivate()
        return self.get_response(request)
