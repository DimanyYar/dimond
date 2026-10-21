import pytest

from apps.accounts.models import Company, Notify

pytestmark = pytest.mark.django_db


def test_pm_opens_their_own_account(client, pm):
    Company.objects.create(name="Atlas", owner=pm)
    client.force_login(pm)

    page = client.get("/account/").content.decode()

    assert "Your account" in page
    assert "PM · 1 client" in page
    assert "New requests, replies and asks to withdraw" in page
    assert "My requests and my team’s" not in page
    assert "Leaving" not in page


def test_pm_menu_leads_to_the_account(client, pm):
    client.force_login(pm)

    assert 'href="/account/"' in client.get("/clients/").content.decode()


def test_pm_turns_their_emails_off(client, pm):
    client.force_login(pm)

    client.post("/account/emails/", {"notify": "none"})

    pm.refresh_from_db()
    assert pm.notify == Notify.NONE


def test_pm_cannot_pick_a_clients_choice(client, pm):
    client.force_login(pm)

    page = client.post("/account/emails/", {"notify": "company"}).content.decode()

    pm.refresh_from_db()
    assert pm.notify == Notify.OWN
    assert "Pick one of these." in page


def test_pm_changes_their_name(client, pm):
    client.force_login(pm)

    client.post("/account/name/", {"name": "Dmitry S."})

    pm.refresh_from_db()
    assert pm.name == "Dmitry S."
