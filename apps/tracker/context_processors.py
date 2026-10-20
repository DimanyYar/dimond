from django.http import HttpRequest

from apps.accounts.models import User
from apps.accounts.selectors import has_clients_page

from .selectors import SyncState, sync_state


def sync(request: HttpRequest) -> dict[str, SyncState]:
    """The sync line and the Logs badge sit on every PM page (mockup: "Jira synced 4 min
    ago" in the sidebar), so every PM page knows the sync's state."""
    user = getattr(request, "user", None)
    if not isinstance(user, User) or not has_clients_page(user):
        return {}
    return {"sync": sync_state()}
