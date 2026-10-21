from django.db.models import Q

from apps.accounts.models import Notify, Role, User
from apps.requests.models import Request

from .models import Email


def update_recipients(request: Request) -> list[User]:
    """The client's people who get emails about this request (PRD: email notifications).

    Read by the worker, not on anyone's behalf: people with access who have signed in at least
    once and did not pick Nothing; "My requests" only for the request's author. Someone invited
    but not signed in yet gets nothing.
    """
    people = User.objects.filter(
        company_id=request.company_id,
        role=Role.CLIENT,
        is_active=True,
        last_login__isnull=False,
    ).exclude(notify=Notify.NONE)
    return [
        person
        for person in people.order_by("pk")
        if person.notify == Notify.COMPANY or person.pk == request.author_id
    ]


def pm_recipient(request: Request) -> User | None:
    """The PM who owns the request's client (ADR 0014), unless they turned these emails off
    on their account page."""
    pm = request.company.owner
    if not pm.is_active or pm.notify == Notify.NONE:
        return None
    return pm


# The Emails tab shows the latest emails; older ones stay in the admin.
EMAILS_SHOWN = 200


def emails_for(pm: User) -> list[Email]:
    """Logs · Emails: every email sent, or tried, to this PM's clients' people and to the PM
    (ADR 0014). An email to someone since deleted (a cancelled invite) has no owner to show
    it to; the admin still lists it."""
    return list(
        Email.objects.filter(Q(user=pm) | Q(user__company__owner=pm))
        .select_related("user__company")
        .order_by("-created_at", "-pk")[:EMAILS_SHOWN]
    )
