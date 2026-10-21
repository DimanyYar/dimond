from dataclasses import dataclass
from datetime import datetime

from django.contrib.sessions.models import Session
from django.db import models
from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from apps.requests.models import CLOSED_STATUSES, Message, Request

from .models import Company, Device, LinkKind, Role, SignInLink, User
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
    def awaits_invite(self) -> bool:
        """Never signed in and still has access: the invite can go again or be called off."""
        return self.status in (
            PersonStatus.INVITED,
            PersonStatus.LINK_EXPIRED,
            PersonStatus.NOT_INVITED,
        )

    @property
    def initials(self) -> str:
        return self.person.initials


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


def _people_of(company: Company) -> list[PersonRow]:
    """Everyone at the company, with access first, people who lost it last. Private: the
    company comes from `clients_page`, already limited to the PM's own clients."""
    rows = _rows(list(company.people.order_by("name", "email")))
    rows.sort(key=lambda row: PERSON_STATUS_ORDER.index(row.status))
    return rows


def _rows(people: list[User]) -> list[PersonRow]:
    now = timezone.now()
    last_invites: dict[int, SignInLink] = {}
    for link in SignInLink.objects.filter(user__in=people, kind=LinkKind.INVITE).order_by(
        "created_at"
    ):
        last_invites[link.user_id] = link
    return [_person_row(person, last_invites.get(person.pk), now) for person in people]


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
        people=_people_of(chosen) if chosen else [],
        picked=pk is not None,
    )


def email_has_account(email: str) -> bool:
    """Whether anyone at all signs in with this address: one email is one person (ADR 0011).

    Only the PM's invite form asks, and the answer is never which company: a PM is Shakuro
    staff and must learn an address cannot be invited (ADR 0014). The sign-in page, open to
    anyone, never reveals it (ADR 0003).
    """
    return User.objects.filter(email=email.strip().lower()).exists()


def client_for(user: User, pk: int) -> Company:
    """One of this PM's own clients. Raises Company.DoesNotExist for anyone else's, exactly as
    for a missing one."""
    return clients_for(user).get(pk=pk)


def person_at_client(user: User, company_pk: int, person_pk: int) -> User:
    """A person at one of this PM's own clients. Raises User.DoesNotExist otherwise."""
    return client_for(user, company_pk).people.get(pk=person_pk)


def client_for_name(user: User, name: str) -> Company | None:
    """This PM's client with this name, whatever the letter case. Other PMs' clients never
    count: a clash with them could not be fixed and would reveal them (ADR 0014)."""
    return clients_for(user).filter(name__iexact=name.strip()).first()


def next_client_color(user: User, colors: list[tuple[str, str]]) -> str:
    """The first UI-kit colour none of this PM's clients wears yet, so a new client stands
    out in the list; the first one again once all are taken."""
    used = set(clients_for(user).values_list("color", flat=True))
    return next((hex_ for hex_, _ in colors if hex_ not in used), colors[0][0])


@dataclass(frozen=True)
class PersonPage:
    company: Company
    row: PersonRow
    invited_by: User | None
    requests_created: int
    replies: int
    last_request: Request | None
    devices: list[Device]


def person_page(user: User, company_pk: int, person_pk: int) -> PersonPage:
    """A person at one of this PM's own clients, with what they did in dimond. Raises
    Company.DoesNotExist or User.DoesNotExist for anyone else, exactly as for a missing one."""
    company = client_for(user, company_pk)
    person = company.people.get(pk=person_pk)
    requests = Request.objects.filter(author=person)
    return PersonPage(
        company=company,
        row=_rows([person])[0],
        invited_by=_inviter(person),
        requests_created=requests.count(),
        replies=Message.objects.filter(author=person).count(),
        last_request=requests.order_by("-created_at").first(),
        devices=_signed_in_on(person),
    )


def _inviter(person: User) -> User | None:
    """Who first invited this person, if anyone did."""
    first_invite = (
        SignInLink.objects.filter(user=person, kind=LinkKind.INVITE, invited_by__isnull=False)
        .select_related("invited_by")
        .order_by("created_at")
        .first()
    )
    return first_invite.invited_by if first_invite else None


def _signed_in_on(person: User) -> list[Device]:
    """Devices whose session still works, newest first. A signed-out or expired session may
    leave its row until the person's next sign-in tidies up."""
    alive = Session.objects.filter(expire_date__gt=timezone.now()).values("session_key")
    return list(person.devices.filter(session_key__in=alive).order_by("-signed_in_at", "-pk"))


@dataclass(frozen=True)
class AccountPage:
    person: User
    invited_by: User | None
    devices: list[Device]
    this_device: Device | None
    clients: int  # the PM's own clients; 0 for a client


def account_owner(user: User) -> User:
    """Whose account page this is: a client's or a PM's own. Raises User.DoesNotExist for the
    team, who only open media links (PRD: roles)."""
    if user.role not in (Role.CLIENT, Role.PM):
        raise User.DoesNotExist
    return user


def account_page(user: User, session_key: str | None) -> AccountPage:
    """A client's own account (PRD: email notifications), with the devices they are signed in
    on and which of them is this one."""
    person = account_owner(user)
    devices = _signed_in_on(person)
    this_device = next((d for d in devices if d.session_key == session_key), None)
    return AccountPage(
        person=person,
        invited_by=_inviter(person),
        devices=devices,
        this_device=this_device,
        clients=person.clients.count() if person.role == Role.PM else 0,
    )
