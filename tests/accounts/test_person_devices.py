"""Where a person is signed in, and signing them out everywhere (PRD: person page, ADR 0016)."""

from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Company, Device, Role, User
from tests.accounts.test_sign_in import ask_for_link, link_path_from_email

pytestmark = pytest.mark.django_db

HOME = reverse("my_requests")
MAC_CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
IPHONE_SAFARI = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1"
)


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm, email_domain="atlas-crm.com")


@pytest.fixture
def mark(atlas) -> User:
    return User.objects.create_user(
        "mark@atlas-crm.com", name="Mark T.", company=atlas, last_login=timezone.now()
    )


@pytest.fixture
def signed_in_pm(pm) -> Client:
    browser = Client()
    browser.force_login(pm)
    return browser


def sign_in_with_link(person: User, user_agent: str) -> Client:
    browser = Client(headers={"User-Agent": user_agent})
    ask_for_link(browser, person.email)
    browser.post(link_path_from_email())
    return browser


def person_page(client: Client, person: User):
    return client.get(reverse("person", args=[person.company_id, person.pk]))


def test_pm_sees_each_browser_the_person_signed_in_from(signed_in_pm, mark):
    sign_in_with_link(mark, MAC_CHROME)
    sign_in_with_link(mark, IPHONE_SAFARI)

    devices = person_page(signed_in_pm, mark).context["page"].devices

    assert [d.name for d in devices] == ["Safari on iPhone", "Chrome on macOS"]


def test_signing_out_on_a_device_removes_it_from_the_list(signed_in_pm, mark):
    laptop = sign_in_with_link(mark, MAC_CHROME)
    sign_in_with_link(mark, IPHONE_SAFARI)

    laptop.post(reverse("sign_out"))

    devices = person_page(signed_in_pm, mark).context["page"].devices
    assert [d.name for d in devices] == ["Safari on iPhone"]


def test_an_expired_session_is_not_listed(signed_in_pm, mark):
    sign_in_with_link(mark, MAC_CHROME)
    Session.objects.filter(session_key__in=mark.devices.values("session_key")).update(
        expire_date=timezone.now() - timedelta(minutes=1)
    )

    assert person_page(signed_in_pm, mark).context["page"].devices == []


def test_sign_out_all_devices_ends_every_session_and_keeps_access(signed_in_pm, mark):
    laptop = sign_in_with_link(mark, MAC_CHROME)
    # Signed in before devices were recorded: no row, still signed out.
    old_phone = Client()
    old_phone.force_login(mark)

    response = signed_in_pm.post(
        reverse("sign_out_devices", args=[mark.company_id, mark.pk]), follow=True
    )

    mark.refresh_from_db()
    assert mark.is_active
    assert "Mark T. is signed out on every device" in response.content.decode()
    for browser in (laptop, old_phone):
        assert browser.get(HOME).url.startswith(reverse("sign_in"))
    assert not Device.objects.filter(user=mark).exists()


def test_after_signing_out_everywhere_a_new_link_works(signed_in_pm, mark):
    sign_in_with_link(mark, MAC_CHROME)
    signed_in_pm.post(reverse("sign_out_devices", args=[mark.company_id, mark.pk]))

    phone = sign_in_with_link(mark, IPHONE_SAFARI)

    assert phone.get(HOME).status_code == 200


def test_revoking_access_empties_the_device_list(signed_in_pm, mark):
    sign_in_with_link(mark, MAC_CHROME)

    services.revoke_access([mark])

    assert person_page(signed_in_pm, mark).context["page"].devices == []


def test_pm_cannot_sign_out_people_of_another_pms_client(signed_in_pm):
    ivan = User.objects.create_user("ivan@shakuro.com", role=Role.PM)
    harbor = Company.objects.create(name="Harbor Foods", owner=ivan)
    paul = User.objects.create_user("paul@harbor.test", company=harbor)
    paul_browser = Client()
    paul_browser.force_login(paul)

    response = signed_in_pm.post(reverse("sign_out_devices", args=[harbor.pk, paul.pk]))

    assert response.status_code == 404
    assert paul_browser.get(HOME).status_code == 200


@pytest.mark.parametrize(
    ("user_agent", "name", "mark"),
    [
        (MAC_CHROME, "Chrome on macOS", "MAC"),
        (IPHONE_SAFARI, "Safari on iPhone", "iOS"),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0",
            "Edge on Windows",
            "WIN",
        ),
        (
            "Mozilla/5.0 (Android 15; Mobile; rv:143.0) Gecko/143.0 Firefox/143.0",
            "Firefox on Android",
            "AND",
        ),
        ("curl/8.5.0", "Browser", "WEB"),
    ],
)
def test_devices_are_named_by_browser_and_system(user_agent, name, mark):
    device = Device(user_agent=user_agent)

    assert (device.name, device.system_mark) == (name, mark)
