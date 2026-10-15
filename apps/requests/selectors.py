from django.db.models import QuerySet

from apps.accounts.models import Role, User

from .models import Request


def requests_for(user: User) -> QuerySet[Request]:
    """Every request this person may see: clients their own company's, the PM all of them.

    The team only opens attachment links, never requests, so they get nothing here.
    """
    requests = Request.objects.select_related("company", "author")
    if not user.is_active:
        return requests.none()
    if user.role == Role.PM:
        return requests
    if user.role == Role.CLIENT:
        return requests.filter(company=user.company)
    return requests.none()


def request_for(user: User, pk: int) -> Request:
    """One request this person may see. Raises Request.DoesNotExist for anyone else's,
    exactly as for a missing one, so guessing ids reveals nothing."""
    return requests_for(user).get(pk=pk)


def can_create_requests(user: User) -> bool:
    """Only clients send requests, and only while they still have access."""
    return user.role == Role.CLIENT and user.is_active
