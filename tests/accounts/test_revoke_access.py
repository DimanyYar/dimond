"""Revoking access from the Clients page and giving it back (PRD: Clients screen, ADR 0015)."""

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Company, LinkKind, Role, SignInLink, User

pytestmark = pytest.mark.django_db

HOME = reverse("my_requests")


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


def device(user: User) -> Client:
    browser = Client()
    browser.force_login(user)
    return browser


def revoke(client, company: Company, person: User):
    return client.post(reverse("revoke_person", args=[company.pk, person.pk]))


def test_revoked_person_is_signed_out_on_every_device(signed_in_pm, atlas, mark):
    laptop, phone = device(mark), device(mark)
    assert laptop.get(HOME).status_code == 200

    revoke(signed_in_pm, atlas, mark)

    for browser in (laptop, phone):
        assert browser.get(HOME).url.startswith(reverse("sign_in"))


def test_old_devices_stay_signed_out_after_access_comes_back(signed_in_pm, atlas, mark):
    laptop = device(mark)
    revoke(signed_in_pm, atlas, mark)

    signed_in_pm.post(reverse("restore_person", args=[atlas.pk, mark.pk]))

    mark.refresh_from_db()
    assert mark.is_active
    assert laptop.get(HOME).url.startswith(reverse("sign_in"))


def test_re_invite_sends_a_fresh_link(signed_in_pm, atlas, mark, pm):
    revoke(signed_in_pm, atlas, mark)

    signed_in_pm.post(reverse("restore_person", args=[atlas.pk, mark.pk]))

    assert mark.sign_in_links.filter(kind=LinkKind.INVITE, invited_by=pm, sent_at=None).exists()


def test_a_link_from_before_the_revoke_does_not_work_again(signed_in_pm, atlas, mark, pm):
    services.invite([mark], invited_by=pm)
    old = SignInLink.objects.get(user=mark)

    revoke(signed_in_pm, atlas, mark)
    signed_in_pm.post(reverse("restore_person", args=[atlas.pk, mark.pk]))

    assert not SignInLink.objects.filter(pk=old.pk).exists()


def test_revoke_keeps_the_persons_requests_and_name(signed_in_pm, atlas, mark):
    revoke(signed_in_pm, atlas, mark)

    mark.refresh_from_db()
    assert not mark.is_active
    assert mark.name == "Mark T."


def test_revoke_asks_first_in_the_persons_row(signed_in_pm, atlas, mark):
    url = reverse("client", args=[atlas.pk]) + f"?revoke={mark.pk}"

    html = signed_in_pm.get(url).content.decode()

    assert "Revoke access for Mark T.?" in html
    assert "signed out on every device" in html


def test_people_without_access_get_re_invite_instead_of_revoke(signed_in_pm, atlas, mark):
    revoke(signed_in_pm, atlas, mark)

    html = signed_in_pm.get(reverse("client", args=[atlas.pk])).content.decode()

    assert reverse("restore_person", args=[atlas.pk, mark.pk]) in html
    assert f"?revoke={mark.pk}" not in html


def test_re_invite_of_someone_with_access_changes_nothing(signed_in_pm, atlas, mark):
    signed_in_pm.post(reverse("restore_person", args=[atlas.pk, mark.pk]))

    assert not mark.sign_in_links.exists()


def test_pm_cannot_revoke_people_of_another_pms_client(signed_in_pm):
    ivan = User.objects.create_user("ivan@shakuro.com", role=Role.PM)
    harbor = Company.objects.create(name="Harbor Foods", owner=ivan)
    paul = User.objects.create_user("paul@harbor.test", company=harbor)

    response = revoke(signed_in_pm, harbor, paul)

    paul.refresh_from_db()
    assert response.status_code == 404
    assert paul.is_active


def test_the_admin_action_signs_out_every_device_too(client, atlas, mark):
    laptop = device(mark)
    admin = User.objects.create_superuser("root@example.com", password="s3cret-pass")
    client.force_login(admin)

    client.post(
        reverse("admin:accounts_user_changelist"),
        {"action": "revoke_access", "_selected_action": [mark.pk]},
    )

    assert laptop.get(HOME).url.startswith(reverse("sign_in"))
