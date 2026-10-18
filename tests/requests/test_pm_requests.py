from datetime import UTC, datetime

import pytest
from django.urls import reverse

from apps.accounts.models import Company, User
from apps.requests import selectors, services
from apps.requests.models import ClientStatus, Message, Request, RequestType, WhenNeeded
from apps.requests.selectors import Period, PmReason, Show

pytestmark = pytest.mark.django_db

URL = reverse("my_requests")


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm)


@pytest.fixture
def northwind(pm) -> Company:
    return Company.objects.create(name="Northwind", owner=pm)


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna")


@pytest.fixture
def mark(atlas) -> User:
    return User.objects.create_user("mark@atlas.test", company=atlas, name="Mark")


@pytest.fixture
def paul(northwind) -> User:
    return User.objects.create_user("paul@northwind.test", company=northwind, name="Paul")


def make(author: User, title: str, status=ClientStatus.RECEIVED) -> Request:
    request = services.create_request(
        author, RequestType.BUG, title, why="Context", when_needed=WhenNeeded.NOT_SURE
    )
    Request.objects.filter(pk=request.pk).update(status=status)
    request.refresh_from_db()
    return request


def reasons(pm: User) -> dict[str, PmReason]:
    found = selectors.pm_reasons(selectors.requests_for(pm))
    return {Request.objects.get(pk=pk).title: reason for pk, reason in found.items()}


def page(user: User, **filters):
    defaults = {
        "show": Show.OPEN,
        "types": [],
        "period": Period.ALL,
        "with_board": True,
        "now": datetime(2026, 10, 18, tzinfo=UTC),
    }
    return selectors.my_requests_page(user, **(defaults | filters))


def column_of(board, request: Request) -> str:
    [slug] = [g.column.slug for g in board if request in g.requests]
    return slug


def test_pm_sees_requests_of_all_their_clients(client, pm, anna, paul):
    make(anna, "Atlas bug")
    make(paul, "Northwind bug")
    client.force_login(pm)

    titles = {r.title for r in client.get(URL).context["page"].requests}

    assert titles == {"Atlas bug", "Northwind bug"}


def test_client_chip_narrows_the_page_to_one_client(pm, anna, paul, atlas, northwind):
    make(anna, "Atlas bug")
    make(paul, "Northwind bug")

    result = page(pm, company=northwind.pk)

    assert [r.title for r in result.requests] == ["Northwind bug"]
    assert [(chip.company.name, chip.needs_pm) for chip in result.clients] == [
        ("Atlas", 1),
        ("Northwind", 1),
    ]


def test_client_cannot_see_another_company_through_the_client_chip(client, anna, paul, atlas):
    make(anna, "Atlas bug")
    client.force_login(paul)

    response = client.get(URL, {"company": atlas.pk})

    assert response.context["page"].requests == []


def test_new_request_waits_for_the_pm_as_new(pm, anna):
    make(anna, "Fresh")

    assert reasons(pm) == {"Fresh": PmReason.NEW}


def test_pm_reply_takes_the_request_out_of_needs_you(pm, anna):
    fresh = make(anna, "Fresh")

    services.post_message(pm, fresh, "On it")

    assert reasons(pm) == {}


def test_client_reply_puts_a_request_in_work_into_needs_you_without_changing_its_status(pm, anna):
    building = make(anna, "Being built", status=ClientStatus.IN_PROGRESS)
    services.post_message(pm, building, "In code review")
    services.post_message(anna, building, "Can Unknown go at the bottom?")

    result = page(pm)

    assert reasons(pm) == {"Being built": PmReason.REPLIED}
    assert column_of(result.board, building) == "needs-you"
    building.refresh_from_db()
    assert building.status == ClientStatus.IN_PROGRESS


def test_a_colleague_message_also_counts_as_client_replied(pm, anna, mark):
    building = make(anna, "Being built", status=ClientStatus.IN_PROGRESS)
    services.post_message(pm, building, "In code review")
    services.post_message(mark, building, "Same here")

    assert reasons(pm) == {"Being built": PmReason.REPLIED}


def test_asks_to_withdraw_come_first_in_the_pm_banner(pm, anna):
    make(anna, "Fresh")
    planned = make(anna, "Planned one", status=ClientStatus.PLANNED)
    services.withdraw_request(anna, planned, "No longer needed")

    shown = page(pm).pm_needs.shown

    assert [(r.title, reason) for r, reason in shown] == [
        ("Planned one", PmReason.WITHDRAW),
        ("Fresh", PmReason.NEW),
    ]


def test_closed_requests_never_wait_for_the_pm(pm, anna):
    done = make(anna, "Shipped", status=ClientStatus.DONE)
    # Written before it closed: nobody can write in a closed request any more.
    Message.objects.create(request=done, author=anna, text="Thanks!")

    assert reasons(pm) == {}


def test_request_waiting_on_the_client_sits_in_queued_for_the_pm(client, pm, anna):
    question = make(anna, "Needs a link")
    services.post_message(pm, question, "Which lead?", ask_for_input=True)
    client.force_login(pm)

    response = client.get(URL)

    assert column_of(response.context["page"].board, question) == "queued"
    assert "Waiting on client" in response.content.decode()


def test_client_board_keeps_needs_you_for_the_pms_questions(client, pm, anna):
    question = make(anna, "Needs a link")
    services.post_message(pm, question, "Which lead?", ask_for_input=True)
    fresh = make(anna, "Fresh")
    client.force_login(anna)

    board = client.get(URL).context["page"].board

    assert column_of(board, question) == "needs-you"
    assert column_of(board, fresh) == "queued"


def test_search_finds_a_request_by_key_whatever_its_age_or_status(pm, anna):
    old = make(anna, "Wrong timezone", status=ClientStatus.DONE)
    make(anna, "Something else")

    by_key = page(pm, query=f"dmn-{old.pk}", period=Period.WEEK)
    by_title = page(pm, query="timezone")

    assert [r.title for r in by_key.requests] == ["Wrong timezone"]
    assert [r.title for r in by_title.requests] == ["Wrong timezone"]


def test_client_search_by_key_never_finds_another_company_request(anna, paul):
    theirs = make(anna, "Atlas only")

    assert page(paul, query=theirs.key).requests == []


def test_pm_card_says_why_the_request_waits(client, pm, anna):
    fresh = make(anna, "Fresh")
    client.force_login(pm)

    response = client.get(fresh.get_absolute_url())

    assert response.context["page"].pm_reason == PmReason.NEW
    assert "Requests</a> / <a" in response.content.decode()


def test_search_says_how_many_it_found(client, anna):
    make(anna, "Wrong timezone on reminders")
    make(anna, "Something else")
    client.force_login(anna)

    html = client.get(URL, {"q": "timezone"}).content.decode()

    assert "1 found for “timezone”" in html


def test_a_request_closed_while_an_ask_to_withdraw_waits_leaves_needs_you(pm, anna):
    planned = make(anna, "Planned one", status=ClientStatus.PLANNED)
    services.withdraw_request(anna, planned, "No longer needed")
    # The Jira sync closes it before the PM decides.
    Request.objects.filter(pk=planned.pk).update(status=ClientStatus.DONE)

    assert reasons(pm) == {}


def test_search_stays_within_the_chosen_client(pm, anna, paul, atlas):
    make(anna, "Wrong timezone at Atlas")
    make(paul, "Wrong timezone at Northwind")

    found = page(pm, query="timezone", company=atlas.pk)

    assert [r.title for r in found.requests] == ["Wrong timezone at Atlas"]
