import re
from datetime import timedelta

import pytest
from django.conf import settings
from django.core import mail
from django.utils import timezone

from apps.accounts.models import Company, SignInLink, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def anna() -> User:
    atlas = Company.objects.create(name="Atlas", email_domain="atlas-crm.com")
    return User.objects.create_user("anna.k@atlas-crm.com", name="Anna K.", company=atlas)


def ask_for_link(client, email: str):
    return client.post("/sign-in/", {"email": email}, follow=True)


def link_path_from_email() -> str:
    url = re.search(r"https?://\S+", mail.outbox[-1].body).group()
    return url.removeprefix(settings.SITE_URL)


def page_without(response, email: str) -> str:
    """The page text with the typed email and the random CSRF token taken out."""
    page = response.content.decode().replace(email, "")
    return re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", page)


def test_anonymous_visitor_is_sent_to_sign_in(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response.url.startswith("/sign-in/")


def test_invited_person_gets_a_sign_in_link_by_email(client, anna):
    response = ask_for_link(client, "Anna.K@atlas-crm.com")

    assert b"Check your inbox" in response.content
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["anna.k@atlas-crm.com"]
    assert settings.SITE_URL in mail.outbox[0].body


def test_sign_in_page_does_not_reveal_who_has_an_account(client, anna):
    known = ask_for_link(client, "anna.k@atlas-crm.com")
    unknown = ask_for_link(client, "stranger@example.com")

    assert len(mail.outbox) == 1
    assert page_without(known, "anna.k@atlas-crm.com") == page_without(
        unknown, "stranger@example.com"
    )


def test_link_signs_the_person_in(client, anna):
    ask_for_link(client, anna.email)

    response = client.post(link_path_from_email(), follow=True)

    assert b"Hi, Anna K." in response.content


def test_opening_the_link_does_not_spend_it(client, anna):
    # Mail scanners open links before the person does; only the button press counts.
    ask_for_link(client, anna.email)
    path = link_path_from_email()

    client.get(path)
    response = client.post(path, follow=True)

    assert b"Hi, Anna K." in response.content


def test_link_works_only_once(client, anna):
    ask_for_link(client, anna.email)
    path = link_path_from_email()
    client.post(path)
    client.post("/sign-out/")

    response = client.post(path)

    assert b"This link has expired" in response.content
    assert "_auth_user_id" not in client.session


def test_link_expires_after_15_minutes(client, anna):
    ask_for_link(client, anna.email)
    SignInLink.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    response = client.post(link_path_from_email())

    assert b"This link has expired" in response.content


def test_person_whose_access_was_revoked_cannot_use_an_old_link(client, anna):
    ask_for_link(client, anna.email)
    anna.is_active = False
    anna.save()

    response = client.post(link_path_from_email())

    assert b"This link has expired" in response.content


def test_revoked_person_gets_no_new_links(client, anna):
    anna.is_active = False
    anna.save()

    ask_for_link(client, anna.email)

    assert mail.outbox == []


def test_link_is_stored_only_as_a_hash(client, anna):
    ask_for_link(client, anna.email)
    token = link_path_from_email().rstrip("/").rsplit("/", 1)[-1]

    assert not SignInLink.objects.filter(token_hash=token).exists()


def test_no_more_than_three_links_in_15_minutes(client, anna):
    for _ in range(4):
        ask_for_link(client, anna.email)

    assert len(mail.outbox) == 3


def test_session_lasts_30_days(client, anna):
    ask_for_link(client, anna.email)

    response = client.post(link_path_from_email())

    assert response.cookies["sessionid"]["max-age"] == 60 * 60 * 24 * 30


def test_sign_out_ends_the_session(client, anna):
    client.force_login(anna)

    client.post("/sign-out/")

    assert client.get("/").status_code == 302
