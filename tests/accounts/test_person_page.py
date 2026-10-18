"""The PM's page for one person at their client (PRD: Clients screen, person page)."""

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Company, LinkKind, Notify, Role, SignInLink, User
from apps.requests.models import RequestType, WhenNeeded
from apps.requests.services import create_request

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm, email_domain="atlas-crm.com")


@pytest.fixture
def mark(atlas) -> User:
    return User.objects.create_user(
        "mark@atlas-crm.com", name="Mark T.", company=atlas, last_login=timezone.now()
    )


@pytest.fixture
def signed_in_pm(client, pm):
    client.force_login(pm)
    return client


def url(name: str, person: User, suffix: str = "") -> str:
    return reverse(name, args=[person.company_id, person.pk]) + suffix


def test_person_page_shows_who_invited_them_and_what_they_did(signed_in_pm, pm, mark):
    services.invite([mark], invited_by=pm)
    request = create_request(
        mark, RequestType.BUG, "Export fails", why="x", when_needed=WhenNeeded.NOT_SURE
    )

    response = signed_in_pm.get(url("person", mark))

    page = response.context["page"]
    assert (page.invited_by, page.requests_created, page.last_request) == (pm, 1, request)
    assert request.key in response.content.decode()


def test_pm_renames_a_person_and_changes_their_emails(signed_in_pm, mark):
    signed_in_pm.post(url("save_person", mark), {"name": "Mark Thompson", "notify": "none"})

    mark.refresh_from_db()
    assert (mark.name, mark.notify) == ("Mark Thompson", Notify.NONE)


def test_a_wrong_choice_is_not_saved(signed_in_pm, mark):
    response = signed_in_pm.post(url("save_person", mark), {"name": "Mark", "notify": "all"})

    mark.refresh_from_db()
    html = response.content.decode()
    assert "Not saved" in html
    assert "Pick one of these." in html
    assert mark.name == "Mark T."


def test_a_too_long_name_is_explained_next_to_the_name(signed_in_pm, mark):
    response = signed_in_pm.post(url("save_person", mark), {"name": "M" * 121, "notify": "own"})

    mark.refresh_from_db()
    assert "at most 120 characters" in response.content.decode()
    assert mark.name == "Mark T."


def test_pm_sends_a_sign_in_link_to_someone_who_signed_in_before(signed_in_pm, mark):
    signed_in_pm.post(url("send_sign_in_link", mark))

    assert mark.sign_in_links.filter(kind=LinkKind.SIGN_IN).exists()


def test_no_sign_in_link_for_someone_who_has_not_signed_in_yet(signed_in_pm, atlas):
    sam = User.objects.create_user("sam@atlas-crm.com", company=atlas)

    response = signed_in_pm.post(url("send_sign_in_link", sam), follow=True)

    assert not sam.sign_in_links.exists()
    assert "No link for sam@atlas-crm.com" in response.content.decode()


def test_sign_in_links_from_the_pm_share_the_limit_of_the_sign_in_page(signed_in_pm, mark):
    for _ in range(5):
        signed_in_pm.post(url("send_sign_in_link", mark))

    assert mark.sign_in_links.count() == 3


def test_revoke_from_the_person_page_asks_first_and_comes_back_here(signed_in_pm, mark):
    html = signed_in_pm.get(url("person", mark, "?revoke")).content.decode()
    assert "Revoke access for Mark T.?" in html

    response = signed_in_pm.post(url("revoke_person", mark), {"back": "person"})

    mark.refresh_from_db()
    assert not mark.is_active
    assert response.url == url("person", mark)


def test_re_invite_from_the_person_page_comes_back_here(signed_in_pm, mark):
    services.revoke_access([mark])

    response = signed_in_pm.post(url("restore_person", mark), {"back": "person"})

    assert response.url == url("person", mark)
    assert mark.sign_in_links.filter(kind=LinkKind.INVITE).exists()


def test_clients_list_links_each_person_to_their_page(signed_in_pm, mark):
    html = signed_in_pm.get(reverse("client", args=[mark.company_id])).content.decode()

    assert url("person", mark) in html


def test_pm_cannot_open_a_person_of_another_pms_client(signed_in_pm):
    ivan = User.objects.create_user("ivan@shakuro.com", role=Role.PM)
    harbor = Company.objects.create(name="Harbor Foods", owner=ivan)
    paul = User.objects.create_user("paul@harbor.test", name="Paul", company=harbor)

    page = signed_in_pm.get(url("person", paul))
    save = signed_in_pm.post(url("save_person", paul), {"name": "X", "notify": "own"})
    link = signed_in_pm.post(url("send_sign_in_link", paul))

    paul.refresh_from_db()
    assert (page.status_code, save.status_code, link.status_code) == (404, 404, 404)
    assert paul.name == "Paul"
    assert not SignInLink.objects.filter(user=paul).exists()


def test_a_person_cannot_be_opened_under_another_company(signed_in_pm, pm, mark):
    bloom = Company.objects.create(name="Bloom", owner=pm)

    response = signed_in_pm.get(reverse("person", args=[bloom.pk, mark.pk]))

    assert response.status_code == 404


def test_clients_cannot_open_person_pages(client, mark):
    client.force_login(mark)

    assert client.get(url("person", mark)).status_code == 404
