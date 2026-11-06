"""Collapsed columns and the phone layout (#30, 30e; canvas 7.3 · PMReleaseViewNav,
PMReleaseViewCompact, MPMReleaseView): the PM narrows the menu or the release list, the choice
stays with the person on every page and device, and on a phone the list hides behind the chosen
release's button. The copy comes from the demo tracker; no test reaches Jira."""

import pytest
from django.urls import reverse

from apps.accounts.models import Company, User
from apps.tracker.mock import MockTracker
from apps.tracker.services import copy_releases

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    company = Company.objects.create(name="Atlas", owner=pm, jira_project="FI")
    copy_releases(MockTracker())
    return company


def collapse(client, column: str, collapsed: str = "1", **extra):
    data = {"column": column, "collapsed": collapsed, "next": "/releases/?mode=sprints"}
    return client.post(reverse("collapse_column"), data, **extra)


def open_page(client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200
    return response.content.decode()


def test_a_collapsed_menu_stays_collapsed_on_every_page(client, pm, atlas):
    client.force_login(pm)

    response = collapse(client, "menu")

    assert response.status_code == 302
    assert response["Location"] == "/releases/?mode=sprints"
    pm.refresh_from_db()
    assert pm.menu_collapsed
    for path in ("/requests/", "/clients/", reverse("releases")):
        assert 'data-column="menu" data-collapsed' in open_page(client, path)


def test_the_menu_widens_again(client, pm):
    pm.menu_collapsed = True
    pm.save()
    client.force_login(pm)

    collapse(client, "menu", collapsed="0")

    pm.refresh_from_db()
    assert not pm.menu_collapsed
    page = open_page(client, "/clients/")
    assert "data-collapsed" not in page
    assert 'aria-label="Collapse the menu"' in page


def test_a_collapsed_release_list_is_kept_for_the_sprint_list_too(client, pm, atlas):
    client.force_login(pm)

    collapse(client, "release_list")

    page = open_page(client, reverse("releases") + "?mode=sprints")
    assert 'data-column="release_list" data-collapsed' in page
    assert 'aria-label="Expand the sprint list"' in page
    assert 'data-column="menu" data-collapsed' not in page


def test_the_choice_belongs_to_the_person_not_the_browser(client, pm, atlas):
    other = User.objects.create_user("olga@shakuro.com", name="Olga", role=pm.role)
    client.force_login(pm)
    collapse(client, "release_list")

    client.force_login(other)

    assert "data-collapsed" not in open_page(client, reverse("releases"))


def test_the_page_script_gets_a_quiet_answer(client, pm):
    client.force_login(pm)

    response = collapse(client, "menu", headers={"X-Requested-With": "fetch"})

    assert response.status_code == 204
    pm.refresh_from_db()
    assert pm.menu_collapsed


def test_an_address_on_another_site_is_not_followed(client, pm):
    client.force_login(pm)

    response = client.post(
        reverse("collapse_column"),
        {"column": "menu", "collapsed": "1", "next": "https://evil.example/"},
    )

    assert response["Location"] == reverse("my_requests")


def test_an_unknown_column_is_refused(client, pm):
    client.force_login(pm)

    assert collapse(client, "inbox").status_code == 400


def test_a_client_has_no_columns_to_collapse(client, atlas):
    anna = User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")
    client.force_login(anna)

    assert collapse(client, "menu").status_code == 404
    assert 'action="/account/columns/"' not in open_page(client, "/requests/")
    anna.refresh_from_db()
    assert not anna.menu_collapsed


def test_on_a_phone_the_chosen_release_opens_the_list(client, pm, atlas):
    client.force_login(pm)

    page = open_page(client, reverse("releases"))

    assert 'aria-controls="release-list" data-release-picker' in page
    assert 'id="release-list"' in page
