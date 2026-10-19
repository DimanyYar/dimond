"""Existing task: the PM links a task already in the client's Jira project (PRD: Existing task;
mockup 5.1 · Existing task), and finds requests by their Jira key. The demo tracker answers."""

import pytest
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.requests import selectors, services
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm, jira_project="FI")


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")


def make(author: User, title: str = "Duplicate leads") -> Request:
    return services.create_request(
        author,
        RequestType.BUG,
        title,
        why="Context",
        description="Twice",
        link="https://crm.atlas.test",
        when_needed=WhenNeeded.HOTFIX,
    )


def existing_tab(client, user: User, request: Request, query: str = ""):
    client.force_login(user)
    url = reverse("request_jira_task", args=[request.pk]) + "?mode=existing"
    return client.get(url + (f"&q={query}" if query else ""))


def link(client, user: User, request: Request, key: str, **fields):
    client.force_login(user)
    data = {"key": key, "reply": "on", "reply_text": "It's already on our list."}
    return client.post(reverse("request_link_jira_task", args=[request.pk]), data | fields)


def test_the_tab_lists_open_tasks_of_the_clients_project(client, pm, anna):
    page = existing_tab(client, pm, make(anna)).content.decode()

    assert "FI-2231" in page
    assert "FI-2190" in page
    assert "FI-2150" not in page  # done
    assert "NW-14" not in page  # another client's project


def test_the_search_finds_tasks_by_words_or_key(client, pm, anna):
    request = make(anna)

    by_words = existing_tab(client, pm, request, "phone").content.decode()
    assert "FI-2208" in by_words
    assert "FI-2231" not in by_words

    by_key = existing_tab(client, pm, request, "fi-2197").content.decode()
    assert "1 found" in by_key
    assert "FI-2197" in by_key


def test_pm_links_a_task_and_the_request_is_planned(client, pm, anna):
    request = make(anna)

    response = link(client, pm, request, "FI-2208")

    request.refresh_from_db()
    assert response.url == request.get_absolute_url()
    assert (request.status, request.jira_key) == (ClientStatus.PLANNED, "FI-2208")
    assert request.jira_url == "https://jira.example.com/browse/FI-2208"
    assert request.messages.get().text == "It's already on our list."
    client.force_login(anna)
    assert "Planned · FI-2208" in client.get(request.get_absolute_url()).content.decode()


def test_a_task_of_another_project_cannot_be_linked(client, pm, anna):
    request = make(anna)

    response = link(client, pm, request, "NW-14")

    assert "NW-14 is not in Atlas’s project (FI)." in response.content.decode()
    request.refresh_from_db()
    assert request.jira_key == ""


def test_a_finished_task_cannot_be_linked(client, pm, anna):
    response = link(client, pm, make(anna), "FI-2150")

    assert "FI-2150 is already done in Jira." in response.content.decode()


def test_a_key_jira_does_not_know_is_explained(client, pm, anna):
    response = link(client, pm, make(anna), "FI-9999")

    assert "There is no task FI-9999" in response.content.decode()


def test_one_task_belongs_to_one_request(client, pm, anna):
    first, second = make(anna, "First"), make(anna, "Second")
    link(client, pm, first, "FI-2208")

    page = existing_tab(client, pm, second).content.decode()
    assert f"linked to {first.key}" in page
    assert 'value="FI-2208" disabled' in page

    response = link(client, pm, second, "FI-2208")
    assert "FI-2208 already belongs to another request." in response.content.decode()


def test_only_the_clients_own_pm_links_tasks(client, anna):
    other_pm = User.objects.create_user("olga@shakuro.com", name="Olga", role=Role.PM)
    request = make(anna)

    assert link(client, other_pm, request, "FI-2208").status_code == 404
    assert link(client, anna, request, "FI-2208").status_code in (403, 404)
    request.refresh_from_db()
    assert request.jira_key == ""


def test_requests_are_found_by_their_jira_key(client, pm, anna):
    request = make(anna)
    link(client, pm, request, "FI-2208")

    assert list(selectors.search(pm, "fi-2208")) == [request]
    assert list(selectors.search(anna, "FI-2208")) == [request]
    assert list(selectors.search(pm, "FI-2190")) == []
