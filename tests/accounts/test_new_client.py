"""The PM adds a client from the Clients page (PRD: Clients screen; mockup 5.0 · New client)."""

import pytest
from django.urls import reverse

from apps.accounts.models import Company, LinkKind, Role, SignInLink, User

pytestmark = pytest.mark.django_db

URL = reverse("new_client")


@pytest.fixture
def signed_in_pm(client, pm):
    client.force_login(pm)
    return client


def create(client, **fields):
    data = {
        "name": "Harbor Foods",
        "email_domain": "",
        "color": "#6e56cf",
        "emails": "",
        "notify": "own",
    }
    return client.post(URL, data | fields)


def test_pm_adds_a_client_that_is_theirs(signed_in_pm, pm):
    response = create(signed_in_pm, email_domain="@HarborFoods.com")

    harbor = Company.objects.get(name="Harbor Foods")
    assert response.url == reverse("client", args=[harbor.pk])
    assert (harbor.owner, harbor.email_domain, harbor.color) == (pm, "harborfoods.com", "#6e56cf")


def test_first_people_are_invited_with_the_client(signed_in_pm, pm):
    create(
        signed_in_pm,
        email_domain="harborfoods.com",
        emails="rita@harborfoods.com, sam@harborfoods.com",
        notify="company",
    )

    people = User.objects.filter(company__name="Harbor Foods")
    assert {p.email for p in people} == {"rita@harborfoods.com", "sam@harborfoods.com"}
    assert all(p.role == Role.CLIENT and p.notify == "company" for p in people)
    assert (
        SignInLink.objects.filter(user__in=people, kind=LinkKind.INVITE, invited_by=pm).count() == 2
    )


def test_a_client_can_start_without_people(signed_in_pm):
    create(signed_in_pm)

    assert not User.objects.filter(company__name="Harbor Foods").exists()


def test_an_address_outside_the_domain_asks_to_confirm(signed_in_pm):
    fields = {"email_domain": "harborfoods.com", "emails": "dev@nimbus.io"}

    first = create(signed_in_pm, **fields)

    assert "Create anyway" in first.content.decode()
    assert not Company.objects.exists()

    create(signed_in_pm, send_anyway="on", **fields)

    assert User.objects.filter(email="dev@nimbus.io", company__name="Harbor Foods").exists()


def test_the_same_name_twice_is_refused_whatever_the_letter_case(signed_in_pm, pm):
    Company.objects.create(name="Harbor Foods", owner=pm)

    response = create(signed_in_pm, name="harbor foods")

    assert "You already have a client with this name." in response.content.decode()
    assert Company.objects.count() == 1


def test_another_pm_may_have_a_client_with_the_same_name(signed_in_pm):
    ivan = User.objects.create_user("ivan@shakuro.com", role=Role.PM)
    Company.objects.create(name="Harbor Foods", owner=ivan)

    create(signed_in_pm)

    assert Company.objects.filter(name="Harbor Foods").count() == 2


@pytest.mark.parametrize(
    "domain",
    ["https://harborfoods.com", "harbor foods", "harborfoods", "-harbor.com", "harbor-.com"],
)
def test_a_domain_that_is_not_a_domain_is_refused(signed_in_pm, domain):
    response = create(signed_in_pm, email_domain=domain)

    assert "A domain looks like company.com" in response.content.decode()
    assert not Company.objects.exists()


def test_a_wrong_or_taken_address_keeps_the_client_from_being_created(signed_in_pm, pm):
    atlas = Company.objects.create(name="Atlas", owner=pm)
    User.objects.create_user("anna@atlas-crm.com", company=atlas)

    response = create(signed_in_pm, emails="anna@atlas-crm.com")

    assert "Already has a dimon.d account" in response.content.decode()
    assert not Company.objects.filter(name="Harbor Foods").exists()


def test_the_window_suggests_a_color_no_client_wears_yet(signed_in_pm, pm):
    Company.objects.create(name="Atlas", owner=pm, color="#ff5600")

    form = signed_in_pm.get(URL).context["new_client_form"]

    assert form["color"].value() == "#03b2cb"


def test_only_pms_add_clients(client, pm):
    atlas = Company.objects.create(name="Atlas", owner=pm)
    client.force_login(User.objects.create_user("anna@atlas-crm.com", company=atlas))

    assert create(client).status_code == 404
    assert not Company.objects.filter(name="Harbor Foods").exists()


def test_empty_clients_page_leads_to_new_client(signed_in_pm):
    html = signed_in_pm.get(reverse("clients")).content.decode()

    assert URL in html
