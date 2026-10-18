from datetime import UTC, datetime

import pytest
from django.urls import reverse

from apps.accounts.models import Company, User
from apps.requests import selectors, services
from apps.requests.models import ClientStatus, Request, RequestType, StatusEvent, WhenNeeded

pytestmark = pytest.mark.django_db

URL = reverse("my_requests")


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm)


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas-crm.com", company=atlas, name="Anna")


def ask(pm: User, author: User, title: str, question: str, *, type=RequestType.BUG) -> Request:
    request = services.create_request(
        author, type, title, why="Context", when_needed=WhenNeeded.NOT_SURE
    )
    services.post_message(pm, request, question, ask_for_input=True)
    return request


def asked_at(request: Request, day: int) -> None:
    """Pretend the PM asked on that day of October."""
    StatusEvent.objects.filter(request=request, to_status=ClientStatus.NEEDS_INPUT).update(
        created_at=datetime(2026, 10, day, tzinfo=UTC)
    )


def test_no_banner_while_nothing_waits_for_the_client(client, anna):
    services.create_request(anna, RequestType.BUG, "Bug", why="x", when_needed=WhenNeeded.HOTFIX)
    client.force_login(anna)

    assert b"needs-you-list" not in client.get(URL).content


def test_banner_shows_the_request_and_the_pm_question(client, anna, pm):
    bug = ask(pm, anna, "Duplicate leads", "Could you share a link to one of the leads?")
    client.force_login(anna)

    content = client.get(URL).content.decode()

    assert f"{bug.key} · Duplicate leads" in content
    assert "Could you share a link to one of the leads?" in content
    assert f'href="{bug.get_absolute_url()}#reply"' in content


def test_several_waiting_requests_share_one_banner_longest_waiting_first(anna, pm):
    newer = ask(pm, anna, "Newer", "Q1")
    older = ask(pm, anna, "Older", "Q2")
    asked_at(newer, 14)
    asked_at(older, 10)

    banner = selectors.needs_you(anna)

    assert [w.request for w in banner.shown] == [older, newer]
    assert [w.question.text for w in banner.shown] == ["Q2", "Q1"]
    assert banner.more == 0


def test_banner_shows_three_and_counts_the_rest(client, anna, pm):
    for i in range(5):
        ask(pm, anna, f"Request {i}", f"Question {i}")
    client.force_login(anna)

    content = client.get(URL).content.decode()

    assert content.count('class="needs-you-row"') == 3
    assert "5 requests need your input" in content
    assert "+2 more waiting for you" in content


def test_filters_do_not_hide_a_question_from_dimon(client, anna, pm):
    idea = ask(pm, anna, "Old idea", "Still relevant?", type=RequestType.IDEA)
    Request.objects.filter(pk=idea.pk).update(created_at=datetime(2025, 1, 1, tzinfo=UTC))
    client.force_login(anna)

    response = client.get(URL, {"type": "bug", "period": "week"})

    assert idea not in response.context["page"].requests
    assert "Still relevant?" in response.content.decode()


def test_banner_shows_only_the_client_company_requests(anna, pm):
    northwind = Company.objects.create(name="Northwind", owner=pm)
    eve = User.objects.create_user("eve@northwind.com", company=northwind)
    ask(pm, eve, "Northwind bug", "Which page?")

    assert selectors.needs_you(anna).shown == []


def test_answered_request_leaves_the_banner(anna, pm):
    bug = ask(pm, anna, "Bug", "Which lead?")
    services.post_message(anna, bug, "Lead 42")

    assert selectors.needs_you(anna).shown == []
