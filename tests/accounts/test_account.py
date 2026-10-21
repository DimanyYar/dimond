import pytest
from django.test import Client

from apps.accounts.models import Company, Device, Notify, SignInLink, User

pytestmark = pytest.mark.django_db

CHROME_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/130.0 Safari/537.36"
SAFARI_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/604.1"


@pytest.fixture
def anna(pm) -> User:
    atlas = Company.objects.create(name="Atlas", email_domain="atlas-crm.com", owner=pm)
    return User.objects.create_user("anna.k@atlas-crm.com", name="Anna K.", company=atlas)


def signed_in(person: User, user_agent: str) -> Client:
    """A browser signed in as this person, recorded as a device the way sign-in does it."""
    browser = Client()
    browser.force_login(person)
    Device.objects.create(
        user=person, session_key=browser.session.session_key, user_agent=user_agent
    )
    return browser


def test_client_sees_their_own_account(client, anna, pm):
    SignInLink.objects.create(user=anna, kind="invite", invited_by=pm)
    client.force_login(anna)

    page = client.get("/account/").content.decode()

    assert "Your account" in page
    assert "anna.k@atlas-crm.com" in page
    assert "from an invite by Dmitry" in page
    assert "To change it, ask Dmitry" in page
    assert "Leaving Atlas?" in page
    for choice in ("My requests", "My requests and my team’s", "Nothing"):
        assert choice in page


def test_account_page_has_no_following(client, anna):
    client.force_login(anna)

    assert "Follow" not in client.get("/account/").content.decode()


def test_client_menu_leads_to_the_account(client, anna):
    client.force_login(anna)

    page = client.get("/requests/").content.decode()

    assert 'href="/account/"' in page


def test_pm_has_no_client_account_page(client, pm):
    client.force_login(pm)

    assert client.get("/account/").status_code == 404


def test_signed_out_visitor_is_sent_to_sign_in(client):
    response = client.get("/account/")

    assert response.status_code == 302
    assert response.url.startswith("/sign-in/")


def test_client_changes_their_name(client, anna):
    client.force_login(anna)

    response = client.post("/account/name/", {"name": "Anna Kowalski"}, follow=True)

    anna.refresh_from_db()
    assert anna.name == "Anna Kowalski"
    assert "Name saved." in response.content.decode()


def test_name_cannot_be_empty(client, anna):
    client.force_login(anna)

    page = client.post("/account/name/", {"name": "  "}).content.decode()

    anna.refresh_from_db()
    assert anna.name == "Anna K."
    assert "Your name shows on your requests." in page


def test_client_turns_emails_off(client, anna):
    client.force_login(anna)

    client.post("/account/emails/", {"notify": "none"})

    anna.refresh_from_db()
    assert anna.notify == Notify.NONE


def test_client_picks_their_team_requests(client, anna):
    client.force_login(anna)

    client.post("/account/emails/", {"notify": "company"})

    anna.refresh_from_db()
    assert anna.notify == Notify.COMPANY


def test_unknown_email_choice_changes_nothing(client, anna):
    client.force_login(anna)

    page = client.post("/account/emails/", {"notify": "follow"}).content.decode()

    anna.refresh_from_db()
    assert anna.notify == Notify.OWN
    assert "Pick one of these." in page


def test_account_marks_this_device(anna):
    laptop = signed_in(anna, CHROME_MAC)
    signed_in(anna, SAFARI_IPHONE)

    page = laptop.get("/account/").content.decode()

    assert "Chrome on macOS" in page
    assert "Safari on iPhone" in page
    assert page.count("This device") == 1


def test_sign_out_other_devices_keeps_this_one(anna):
    laptop = signed_in(anna, CHROME_MAC)
    phone = signed_in(anna, SAFARI_IPHONE)

    laptop.post("/account/sign-out-others/")

    assert laptop.get("/account/").status_code == 200
    assert phone.get("/account/").status_code == 302
    assert list(anna.devices.values_list("user_agent", flat=True)) == [CHROME_MAC]


def test_sign_out_other_devices_also_ends_sessions_from_before_devices_were_recorded(anna):
    laptop = signed_in(anna, CHROME_MAC)
    old_browser = Client()
    old_browser.force_login(anna)

    laptop.post("/account/sign-out-others/")

    assert old_browser.get("/account/").status_code == 302
    assert laptop.get("/account/").status_code == 200


def test_sign_out_other_devices_is_offered_even_when_only_this_device_is_listed(anna):
    laptop = signed_in(anna, CHROME_MAC)

    assert "Sign out other devices" in laptop.get("/account/").content.decode()


def test_pm_sees_the_new_email_choices_on_the_person_page(client, anna, pm):
    client.force_login(pm)

    page = client.get(f"/clients/{anna.company.pk}/people/{anna.pk}/").content.decode()

    assert "Their requests and their team’s" in page
    assert "follow" not in page.lower()
