import re
from datetime import timedelta

import pytest
from django.conf import settings
from django.core import mail
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Company, Notify, SignInLink, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def dmitry() -> User:
    return User.objects.create_superuser(
        "dmitry@shakuro.com", password="s3cret-pass", name="Dmitry"
    )


@pytest.fixture
def ruslan(dmitry) -> User:
    atlas = Company.objects.create(name="Atlas", email_domain="atlas-crm.com", owner=dmitry)
    return User.objects.create_user("ruslan.v@atlas-crm.com", company=atlas)


def invite_path(person: User, by: User) -> str:
    services.invite([person], invited_by=by)
    services.send_queued_links()
    url = re.search(r"https?://\S+", mail.outbox[-1].body).group()
    return url.removeprefix(settings.SITE_URL)


def test_invited_person_gets_an_email_naming_who_invited_them(ruslan, dmitry):
    invite_path(ruslan, dmitry)

    assert mail.outbox[0].to == ["ruslan.v@atlas-crm.com"]
    assert "Dmitry invited you" in mail.outbox[0].body
    assert "7 days" in mail.outbox[0].body


def test_invite_email_has_a_styled_version_with_the_same_link(ruslan, dmitry):
    path = invite_path(ruslan, dmitry)

    html, kind = mail.outbox[0].alternatives[0]
    assert kind == "text/html"
    assert "Dmitry invited you to dimon.d" in html
    assert "This is where Atlas sends requests" in html
    assert f'href="{settings.SITE_URL}{path}"' in html


def test_invite_opens_the_welcome_screen_without_spending_the_link(client, ruslan, dmitry):
    path = invite_path(ruslan, dmitry)

    first = client.get(path)
    second = client.get(path)

    assert b"Welcome to dimon.d" in first.content
    assert b"Invited by Dmitry" in second.content


def test_first_sign_in_saves_name_and_notifications(client, ruslan, dmitry):
    path = invite_path(ruslan, dmitry)

    response = client.post(path, {"name": "Ruslan V.", "notify": "company"}, follow=True)

    assert b"Hi, Ruslan V." in response.content
    ruslan.refresh_from_db()
    assert ruslan.name == "Ruslan V."
    assert ruslan.notify == Notify.COMPANY


def test_name_is_required_on_first_sign_in(client, ruslan, dmitry):
    path = invite_path(ruslan, dmitry)

    response = client.post(path, {"name": "", "notify": "own"})

    assert b"Tell us your name" in response.content
    assert "_auth_user_id" not in client.session


def test_invite_works_only_once(client, ruslan, dmitry):
    path = invite_path(ruslan, dmitry)
    client.post(path, {"name": "Ruslan V.", "notify": "own"})
    client.post("/sign-out/")

    response = client.get(path)

    assert b"Welcome to dimon.d" not in response.content
    assert client.post(path).status_code == 200
    assert "_auth_user_id" not in client.session


def test_invite_lasts_7_days(client, ruslan, dmitry):
    path = invite_path(ruslan, dmitry)
    SignInLink.objects.update(expires_at=timezone.now() + timedelta(days=6, hours=23))
    assert b"Welcome to dimon.d" in client.get(path).content

    SignInLink.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    response = client.post(path, {"name": "Ruslan V.", "notify": "own"})
    assert b"This link has expired" in response.content


def test_invite_cannot_be_used_as_a_plain_sign_in_link(ruslan, dmitry):
    token = invite_path(ruslan, dmitry).rstrip("/").rsplit("/", 1)[-1]

    with pytest.raises(services.SignInLinkRejectedError):
        services.use_sign_in_link(token)


def test_people_without_access_are_not_invited(ruslan, dmitry):
    ruslan.is_active = False
    ruslan.save()

    assert services.invite([ruslan], invited_by=dmitry) == 0


def test_invites_do_not_count_toward_the_sign_in_link_limit(ruslan, dmitry):
    for _ in range(4):
        services.invite([ruslan], invited_by=dmitry)

    services.request_sign_in_link(ruslan.email)

    assert ruslan.sign_in_links.filter(kind="sign_in").count() == 1


def test_revoked_person_is_signed_out_on_next_page(client, ruslan, dmitry):
    client.force_login(ruslan)

    services.revoke_access([ruslan])

    assert client.get("/").status_code == 302


def test_pm_invites_and_revokes_from_the_admin(client, ruslan, dmitry):
    client.force_login(dmitry)
    page = f"/{settings.ADMIN_URL}accounts/user/"

    client.post(page, {"action": "send_invite", "_selected_action": [ruslan.pk]})
    services.send_queued_links()
    assert mail.outbox[0].to == [ruslan.email]

    client.post(page, {"action": "revoke_access", "_selected_action": [ruslan.pk]})
    ruslan.refresh_from_db()
    assert not ruslan.is_active
