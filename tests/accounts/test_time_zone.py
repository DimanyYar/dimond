"""Everyone with an account page picks their own time zone, and every time on their pages
shows in it (PRD: account). Times are stored in UTC."""

import re
from datetime import UTC, datetime

import pytest
from django.conf import settings
from django.core import mail

from apps.accounts import services
from apps.accounts.models import Company, User
from apps.requests import services as requests
from apps.requests.models import Request, RequestType, WhenNeeded

pytestmark = pytest.mark.django_db


@pytest.fixture
def anna(pm) -> User:
    atlas = Company.objects.create(name="Atlas", email_domain="atlas-crm.com", owner=pm)
    return User.objects.create_user("anna.k@atlas-crm.com", name="Anna K.", company=atlas)


def request_sent_at(author: User, at: datetime) -> Request:
    request = requests.create_request(
        author, RequestType.BUG, "Export times out", why="Sales", when_needed=WhenNeeded.HOTFIX
    )
    Request.objects.filter(pk=request.pk).update(created_at=at)
    return request


def test_client_picks_their_time_zone(client, anna):
    client.force_login(anna)

    response = client.post("/account/time-zone/", {"time_zone": "America/Los_Angeles"}, follow=True)

    anna.refresh_from_db()
    assert anna.time_zone == "America/Los_Angeles"
    assert "Time zone saved." in response.content.decode()


def test_pm_picks_their_time_zone_too(client, pm):
    client.force_login(pm)

    client.post("/account/time-zone/", {"time_zone": "Europe/Moscow"})

    pm.refresh_from_db()
    assert pm.time_zone == "Europe/Moscow"


def test_unknown_time_zone_is_refused(client, anna):
    client.force_login(anna)

    page = client.post("/account/time-zone/", {"time_zone": "Mars/Olympus"}).content.decode()

    anna.refresh_from_db()
    assert anna.time_zone == ""
    assert "Pick a time zone from the list." in page


def test_account_page_says_times_are_in_utc_until_a_zone_is_saved(client, anna):
    client.force_login(anna)

    page = client.get("/account/").content.decode()

    assert "times show in UTC until you save" in page
    assert '<option value="Europe/Moscow">UTC+03:00 · Europe/Moscow</option>' in page


def test_newcomer_picks_their_time_zone_on_the_welcome_screen(client, anna, pm):
    services.invite([anna], invited_by=pm)
    services.send_queued_links()
    url = re.search(r"https?://\S+", mail.outbox[-1].body).group()
    path = url.removeprefix(settings.SITE_URL)
    assert "Your time zone" in client.get(path).content.decode()

    client.post(path, {"name": "Anna K.", "notify": "own", "time_zone": "Asia/Tokyo"})

    anna.refresh_from_db()
    assert anna.time_zone == "Asia/Tokyo"


def test_times_on_pages_show_in_the_persons_zone(client, anna):
    request = request_sent_at(anna, datetime(2026, 10, 26, 22, 30, tzinfo=UTC))
    client.force_login(anna)
    assert "Oct 26, 22:30" in client.get(request.get_absolute_url()).content.decode()

    services.choose_time_zone(anna, "Europe/Moscow")

    assert "Oct 27, 01:30" in client.get(request.get_absolute_url()).content.decode()


def test_one_persons_zone_does_not_leak_into_the_next_page(client, anna, pm):
    request = request_sent_at(anna, datetime(2026, 10, 26, 22, 30, tzinfo=UTC))
    services.choose_time_zone(anna, "Europe/Moscow")
    client.force_login(anna)
    client.get(request.get_absolute_url())

    client.force_login(pm)

    assert "Oct 26, 22:30" in client.get(request.get_absolute_url()).content.decode()
