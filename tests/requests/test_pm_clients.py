"""Every PM has their own clients and never sees another PM's (PRD: roles)."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.requests import selectors, services
from apps.requests.models import ClientStatus, Message, Request, RequestType, WhenNeeded

pytestmark = pytest.mark.django_db


@pytest.fixture
def ivan() -> User:
    return User.objects.create_user("ivan@shakuro.com", name="Ivan", role=Role.PM)


@pytest.fixture
def anna(pm) -> User:
    atlas = Company.objects.create(name="Atlas", owner=pm)
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna")


@pytest.fixture
def paul(ivan) -> User:
    harbor = Company.objects.create(name="Harbor Foods", owner=ivan)
    return User.objects.create_user("paul@harbor.test", company=harbor, name="Paul")


def make(author: User, title: str) -> Request:
    return services.create_request(
        author, RequestType.BUG, title, why="Context", when_needed=WhenNeeded.NOT_SURE
    )


def test_pm_sees_only_requests_of_their_own_clients(client, pm, anna, paul):
    make(anna, "Atlas bug")
    make(paul, "Harbor bug")
    client.force_login(pm)

    page = client.get(reverse("my_requests")).context["page"]

    assert {r.title for r in page.requests} == {"Atlas bug"}
    assert [chip.company.name for chip in page.clients] == ["Atlas"]
    assert [r.title for r, _ in page.pm_needs.shown] == ["Atlas bug"]


def test_pm_cannot_open_another_pms_client_request(client, pm, paul):
    harbor_bug = make(paul, "Harbor bug")
    client.force_login(pm)

    assert client.get(harbor_bug.get_absolute_url()).status_code == 404


def test_pm_cannot_reply_or_triage_in_another_pms_client_request(client, pm, paul):
    harbor_bug = make(paul, "Harbor bug")
    client.force_login(pm)

    reply = client.post(harbor_bug.get_absolute_url(), {"kind": "reply", "text": "Hi"})
    later = client.post(reverse("request_triage", args=[harbor_bug.pk]), {"action": "later"})

    harbor_bug.refresh_from_db()
    assert (reply.status_code, later.status_code) == (404, 404)
    assert harbor_bug.status == ClientStatus.RECEIVED
    assert not Message.objects.filter(request=harbor_bug).exists()


def test_pm_cannot_find_another_pms_request_by_its_key(pm, paul):
    harbor_bug = make(paul, "Harbor bug")

    assert not selectors.search(pm, f"DMN-{harbor_bug.pk}").exists()


def test_owner_pm_works_with_their_client_as_before(pm, ivan, paul):
    harbor_bug = make(paul, "Harbor bug")

    assert selectors.can_post_message(ivan, harbor_bug)
    assert selectors.can_decline(ivan, harbor_bug)
    assert not selectors.can_post_message(pm, harbor_bug)
    assert not selectors.can_decline(pm, harbor_bug)


def test_new_pm_starts_with_an_empty_portal(client, ivan, anna):
    make(anna, "Atlas bug")
    client.force_login(ivan)

    page = client.get(reverse("my_requests")).context["page"]

    assert page.requests == []
    assert not page.has_any_request


def test_only_a_pm_can_own_a_client(anna):
    company = Company(name="Northwind", owner=anna)

    with pytest.raises(ValidationError, match="Only a PM can own a client"):
        company.full_clean()


def test_pm_who_owns_clients_cannot_become_another_role(ivan, paul):
    ivan.role = Role.TEAM

    with pytest.raises(ValidationError, match="Give them to another PM first"):
        ivan.full_clean()
