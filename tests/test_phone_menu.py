"""On a phone the sidebar is the menu behind the ☰ (mockups PM · phone, Client · phone):
the same links as the desktop sidebar, and the client's + sits in the top bar."""

import pytest

from apps.accounts.models import Company, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def anna(pm) -> User:
    atlas = Company.objects.create(name="Atlas", owner=pm)
    return User.objects.create_user("anna.k@atlas-crm.com", name="Anna K.", company=atlas)


def test_client_page_has_the_menu_button_and_the_new_request_button(client, anna):
    client.force_login(anna)

    page = client.get("/requests/").content.decode()

    assert 'aria-controls="main-menu"' in page
    assert 'class="topbar-new"' in page
    assert 'class="tabbar"' not in page


def test_client_menu_offers_a_new_request(client, anna):
    client.force_login(anna)

    page = client.get("/account/").content.decode()

    assert 'class="nav-phone-only" href="/requests/new/"' in page


def test_pm_menu_lists_every_section(client, pm):
    client.force_login(pm)

    page = client.get("/clients/").content.decode()

    for link in ('href="/requests/"', 'href="/clients/"', 'href="/logs/"', 'href="/account/"'):
        assert link in page
    assert 'class="topbar-new"' not in page
