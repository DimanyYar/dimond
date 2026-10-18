from collections.abc import Callable

from django.http import Http404, HttpRequest, HttpResponse

from .views import not_found


class NotFoundPage:
    """Every "not found" a view raises gets our page, also in development, where Django would
    otherwise show its technical page with the list of addresses. Addresses no view answers
    get the same page through `handler404` in production."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        if isinstance(exception, Http404):
            return not_found(request)
        return None
