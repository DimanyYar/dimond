"""The PM's Clients screen: their companies and the people at each (PRD: Clients screen)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import selectors
from apps.accounts.models import Company, LinkKind, Role, SignInLink, User
from apps.accounts.selectors import PersonStatus
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded
from apps.requests.services import create_request

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm, email_domain="atlas-crm.com")


def person(company: Company, email: str, name: str = "", **fields) -> User:
    return User.objects.create_user(email, name=name, company=company, **fields)


def invite_sent(user: User, *, expires_in: timedelta) -> None:
    now = timezone.now()
    SignInLink.objects.create(
        user=user, kind=LinkKind.INVITE, sent_at=now, expires_at=now + expires_in
    )


def statuses(company: Company) -> list[tuple[str, PersonStatus]]:
    page = selectors.clients_page(company.owner, company.pk)
    return [(row.person.email, row.status) for row in page.people]


def test_pm_sees_only_their_own_clients(client, pm, atlas):
    ivan = User.objects.create_user("ivan@shakuro.com", role=Role.PM)
    Company.objects.create(name="Harbor Foods", owner=ivan)
    client.force_login(pm)

    page = client.get(reverse("clients")).context["page"]

    assert [company.name for company in page.companies] == ["Atlas"]


def test_another_pms_client_is_not_found(client, pm):
    ivan = User.objects.create_user("ivan@shakuro.com", role=Role.PM)
    harbor = Company.objects.create(name="Harbor Foods", owner=ivan)
    client.force_login(pm)

    assert client.get(reverse("client", args=[harbor.pk])).status_code == 404


@pytest.mark.parametrize("role", [Role.CLIENT, Role.TEAM])
def test_only_pms_have_a_clients_page(client, atlas, role):
    company = atlas if role == Role.CLIENT else None
    client.force_login(User.objects.create_user("x@atlas-crm.com", role=role, company=company))

    assert client.get(reverse("clients")).status_code == 404


def test_people_show_where_they_stand_with_access(atlas):
    anna = person(atlas, "anna@atlas-crm.com", "Anna K.", last_login=timezone.now())
    ruslan = person(atlas, "ruslan@atlas-crm.com")
    invite_sent(ruslan, expires_in=timedelta(days=5))
    pavel = person(atlas, "pavel@atlas-crm.com")
    invite_sent(pavel, expires_in=-timedelta(days=1))
    olga = person(atlas, "olga@atlas-crm.com")
    leo = person(atlas, "leo@atlas-crm.com", "Leo V.", is_active=False)

    assert statuses(atlas) == [
        (anna.email, PersonStatus.ACTIVE),
        (ruslan.email, PersonStatus.INVITED),
        (pavel.email, PersonStatus.LINK_EXPIRED),
        (olga.email, PersonStatus.NOT_INVITED),
        (leo.email, PersonStatus.NO_ACCESS),
    ]


def test_a_new_invite_replaces_an_expired_one(atlas):
    ruslan = person(atlas, "ruslan@atlas-crm.com")
    invite_sent(ruslan, expires_in=-timedelta(days=1))
    SignInLink.objects.create(user=ruslan, kind=LinkKind.INVITE)

    assert statuses(atlas) == [(ruslan.email, PersonStatus.INVITED)]


def test_company_list_counts_people_and_open_requests(client, pm, atlas):
    anna = person(atlas, "anna@atlas-crm.com", "Anna", last_login=timezone.now())
    person(atlas, "ruslan@atlas-crm.com")
    person(atlas, "leo@atlas-crm.com", is_active=False)
    for title in ("Export", "Filters"):
        create_request(anna, RequestType.BUG, title, why="x", when_needed=WhenNeeded.NOT_SURE)
    Request.objects.filter(title="Filters").update(status=ClientStatus.DONE)
    client.force_login(pm)

    [listed] = client.get(reverse("clients")).context["page"].companies

    assert (listed.people_signed_in, listed.people_pending, listed.open_requests) == (1, 1, 1)


def test_clients_page_opens_on_the_first_company(client, pm, atlas):
    Company.objects.create(name="Bloom Clinic", owner=pm)
    client.force_login(pm)

    page = client.get(reverse("clients")).context["page"]

    assert page.chosen.name == "Atlas"
    assert not page.picked


def test_company_page_shows_its_people_and_links_to_its_requests(client, pm, atlas):
    person(atlas, "anna@atlas-crm.com", "Anna K.", last_login=timezone.now())
    client.force_login(pm)

    response = client.get(reverse("client", args=[atlas.pk]))

    html = response.content.decode()
    assert response.context["page"].picked
    assert "Anna K." in html
    assert f"/requests/?company={atlas.pk}" in html


def test_pm_navigation_leads_to_clients(client, pm):
    client.force_login(pm)

    assert reverse("clients") in client.get(reverse("my_requests")).content.decode()
