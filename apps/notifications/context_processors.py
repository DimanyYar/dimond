from django.http import HttpRequest

from apps.accounts.models import User
from apps.accounts.selectors import has_clients_page

from .selectors import mail_pause


def mail(request: HttpRequest) -> dict[str, bool]:
    """Paused emails light up the Logs badge on every PM page, like a paused Jira sync."""
    user = getattr(request, "user", None)
    if not isinstance(user, User) or not has_clients_page(user):
        return {}
    return {"mail_paused": mail_pause() is not None}
