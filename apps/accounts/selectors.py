from dataclasses import dataclass
from datetime import datetime

from django.db import models
from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from apps.requests.models import CLOSED_STATUSES

from .models import Company, LinkKind, Role, SignInLink, User
from .services import hash_token


def open_invite(token: str) -> SignInLink | None:
    """The invite behind this token, if it can still be accepted. Reading never spends it."""
    return (
        SignInLink.objects.select_related("user__company", "invited_by")
        .filter(
            token_hash=hash_token(token),
            kind=LinkKind.INVITE,
            used_at__isnull=True,
            expires_at__gt=timezone.now(),
            user__is_active=True,
        )
        .first()
    )


def has_clients_page(user: User) -> bool:
    return user.is_active and user.role == Role.PM


def clients_for(user: User) -> QuerySet[Company]:
    """The companies this PM owns, with the numbers the Clients list shows. Other PMs' clients
    never appear (ADR 0014)."""
    if not has_clients_page(user):
        return Company.objects.none()
    with_access = Q(people__is_active=True)
    return (
        Company.objects.filter(owner=user)
        .annotate(
            people_signed_in=Count(
                "people", filter=with_access & Q(people__last_login__isnull=False), distinct=True
            ),
            people_pending=Count(
                "people", filter=with_access & Q(people__last_login__isnull=True), distinct=True
            ),
            open_requests=Count(
                "requests", filter=~Q(requests__status__in=CLOSED_STATUSES), distinct=True
            ),
        )
        .order_by("name")
    )


class PersonStatus(models.TextChoices):
    """Where a person stands with access (PRD: Clients screen). In the order the list shows them."""

    ACTIVE = "active", "Active"
    INVITED = "invited", "Invited"
    LINK_EXPIRED = "link_expired", "Link expired"
    NOT_INVITED = "not_invited", "Not invited"
    NO_ACCESS = "no_access", "No access"


PERSON_STATUS_ORDER = list(PersonStatus)


@dataclass(frozen=True)
class PersonRow:
    person: User
    status: PersonStatus
    # Signed in at, the invite link's last day, or when it ran out, depending on the status.
    when: datetime | None

    @property
    def initials(self) -> str:
        words = self.person.name.split()
        return "".join(word[0] for word in words[:2]).upper() or "@"


def _person_row(person: User, last_invite: SignInLink | None, now: datetime) -> PersonRow:
    if not person.is_active:
        return PersonRow(person, PersonStatus.NO_ACCESS, None)
    if person.last_login is not None:
        return PersonRow(person, PersonStatus.ACTIVE, person.last_login)
    if last_invite is None:
        return PersonRow(person, PersonStatus.NOT_INVITED, None)
    # A queued invite has no expiry yet: the worker sets it when the email goes out.
    if last_invite.expires_at is None or last_invite.expires_at > now:
        return PersonRow(person, PersonStatus.INVITED, last_invite.expires_at)
    return PersonRow(person, PersonStatus.LINK_EXPIRED, last_invite.expires_at)


def people_of(company: Company) -> list[PersonRow]:
    """Everyone at the company, with access first, people who lost it last."""
    now = timezone.now()
    people = list(company.people.order_by("name", "email"))
    last_invites: dict[int, SignInLink] = {}
    for link in SignInLink.objects.filter(user__in=people, kind=LinkKind.INVITE).order_by(
        "created_at"
    ):
        last_invites[link.user_id] = link
    rows = [_person_row(person, last_invites.get(person.pk), now) for person in people]
    rows.sort(key=lambda row: PERSON_STATUS_ORDER.index(row.status))
    return rows


@dataclass(frozen=True)
class ClientsPage:
    companies: list[Company]
    chosen: Company | None
    people: list[PersonRow]
    # On a phone the list and one company are separate screens: a company the PM picked
    # replaces the list, the bare Clients address shows the list.
    picked: bool


def clients_page(user: User, pk: int | None) -> ClientsPage:
    """The Clients screen. Raises Company.DoesNotExist for a company this PM does not own,
    exactly as for a missing one."""
    companies = list(clients_for(user))
    if pk is None:
        chosen = companies[0] if companies else None
    else:
        chosen = next((company for company in companies if company.pk == pk), None)
        if chosen is None:
            raise Company.DoesNotExist
    return ClientsPage(
        companies=companies,
        chosen=chosen,
        people=people_of(chosen) if chosen else [],
        picked=pk is not None,
    )
