import pytest
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.requests import selectors, services
from apps.requests.models import ClientStatus, Message, Request, RequestType, WhenNeeded

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas() -> Company:
    return Company.objects.create(name="Atlas")


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas-crm.com", company=atlas, name="Anna K.")


@pytest.fixture
def mark(atlas) -> User:
    return User.objects.create_user("mark@atlas-crm.com", company=atlas, name="Mark T.")


@pytest.fixture
def pm() -> User:
    return User.objects.create_user("dmitry@shakuro.com", role=Role.PM, name="Dmitry")


@pytest.fixture
def bug(anna) -> Request:
    return services.create_request(
        anna,
        RequestType.BUG,
        "Duplicate leads from the web form",
        why="Managers call the same person twice.",
        when_needed=WhenNeeded.HOTFIX,
        link="https://crm.example.com/leads/1",
    )


def url(request: Request) -> str:
    return reverse("request_detail", args=[request.pk])


def set_status(request: Request, status: ClientStatus) -> None:
    Request.objects.filter(pk=request.pk).update(status=status)
    request.refresh_from_db()


def test_client_opens_their_company_request_card(client, bug, mark):
    client.force_login(mark)

    response = client.get(url(bug))

    assert response.status_code == 200
    assert b"Duplicate leads from the web form" in response.content
    assert b"Managers call the same person twice." in response.content


def test_client_cannot_open_other_company_request_card(client, bug):
    northwind = Company.objects.create(name="Northwind")
    client.force_login(User.objects.create_user("eve@northwind.com", company=northwind))

    assert client.get(url(bug)).status_code == 404
    assert client.post(url(bug), {"text": "hi"}).status_code == 404
    assert not Message.objects.exists()


def test_team_member_cannot_open_request_card(client, bug):
    client.force_login(User.objects.create_user("dev@shakuro.com", role=Role.TEAM))

    assert client.get(url(bug)).status_code == 404


def test_my_requests_cards_open_the_request(client, bug, anna):
    client.force_login(anna)

    assert url(bug).encode() in client.get(reverse("my_requests")).content
    assert url(bug).encode() in client.get(reverse("my_requests"), {"view": "list"}).content


def test_pm_asks_for_input_and_request_needs_the_client(client, bug, pm, anna):
    client.force_login(pm)

    response = client.post(url(bug), {"text": "Which lead?", "ask_for_input": "on"})

    bug.refresh_from_db()
    assert response.status_code == 302
    assert bug.status == ClientStatus.NEEDS_INPUT
    assert bug.status_events.last().actor == pm
    client.force_login(anna)
    card = client.get(url(bug))
    assert b"Dimon needs one thing from you" in card.content
    assert card.context["page"].question.text == "Which lead?"


def test_client_answer_returns_request_to_pm(client, bug, pm, mark):
    services.post_message(pm, bug, "Which lead?", ask_for_input=True)
    client.force_login(mark)

    client.post(url(bug), {"text": "https://crm.example.com/leads/48213"})

    bug.refresh_from_db()
    last = bug.status_events.last()
    assert bug.status == ClientStatus.RECEIVED
    assert (last.from_status, last.actor) == (ClientStatus.NEEDS_INPUT, mark)
    assert bug.messages.last().author == mark


def test_client_comment_keeps_the_status(bug, anna):
    set_status(bug, ClientStatus.IN_PROGRESS)

    services.post_message(anna, bug, "One more detail")

    bug.refresh_from_db()
    assert bug.status == ClientStatus.IN_PROGRESS


def test_only_pm_asks_for_input_and_only_before_planning(bug, anna, pm):
    with pytest.raises(services.NotAllowedError):
        services.post_message(anna, bug, "Question?", ask_for_input=True)
    set_status(bug, ClientStatus.PLANNED)
    with pytest.raises(services.NotAllowedError):
        services.post_message(pm, bug, "Question?", ask_for_input=True)
    assert not Message.objects.exists()


@pytest.mark.parametrize("status", [ClientStatus.DONE, ClientStatus.DECLINED])
def test_nobody_writes_in_a_closed_request(client, bug, anna, pm, status):
    set_status(bug, status)
    client.force_login(anna)

    assert b"This request is closed." in client.get(url(bug)).content
    assert client.post(url(bug), {"text": "Still broken"}).status_code == 403
    with pytest.raises(services.NotAllowedError):
        services.post_message(pm, bug, "Reopening")


def test_revoked_person_cannot_write(bug, anna):
    anna.is_active = False
    anna.save()

    with pytest.raises(services.NotAllowedError):
        services.post_message(anna, bug, "Hello?")


def test_empty_message_is_rejected_with_a_hint(client, bug, anna):
    client.force_login(anna)

    response = client.post(url(bug), {"text": "   "})

    assert response.status_code == 422
    assert b"Write a message first." in response.content


def test_thread_shows_messages_and_status_changes_in_order(bug, pm, anna):
    services.post_message(pm, bug, "Which lead?", ask_for_input=True)
    services.post_message(anna, bug, "Lead 48213")

    lines = [e.message.text if e.message else e.event for e in selectors.thread(bug)]

    assert lines == [
        "Request received",
        "Which lead?",
        "Waiting for your input",
        "Lead 48213",
        "Back with Dimon",
    ]


def test_progress_marks_earlier_steps_done(bug, anna):
    set_status(bug, ClientStatus.IN_PROGRESS)

    progress = selectors.request_page(anna, bug.pk).progress

    assert [s.state for s in progress.steps] == ["done", "done", "current", "next", "next"]
    assert progress.position == 3


def test_progress_flags_received_while_waiting_for_the_client(bug, pm, anna):
    services.post_message(pm, bug, "Which lead?", ask_for_input=True)

    steps = selectors.request_page(anna, bug.pk).progress.steps

    assert steps[0] == ("Received · needs your input", "waiting")


@pytest.mark.parametrize("status", [ClientStatus.LATER, ClientStatus.DECLINED])
def test_later_and_declined_are_off_the_progress_steps(bug, anna, status):
    set_status(bug, status)

    progress = selectors.request_page(anna, bug.pk).progress

    assert progress.steps == []
    assert progress.off_track
