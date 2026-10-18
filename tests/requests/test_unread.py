from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Company, Role, User
from apps.requests import selectors, services
from apps.requests.models import ClientStatus, Message, Request, RequestType, WhenNeeded

pytestmark = pytest.mark.django_db

MY_REQUESTS = reverse("my_requests")


@pytest.fixture
def atlas() -> Company:
    return Company.objects.create(name="Atlas")


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas-crm.com", company=atlas, name="Anna")


@pytest.fixture
def mark(atlas) -> User:
    return User.objects.create_user("mark@atlas-crm.com", company=atlas, name="Mark")


@pytest.fixture
def pm() -> User:
    return User.objects.create_user("dmitry@shakuro.com", role=Role.PM, name="Dmitry")


@pytest.fixture
def bug(anna) -> Request:
    return services.create_request(
        anna,
        RequestType.BUG,
        "Duplicate leads",
        why="Managers call twice",
        when_needed=WhenNeeded.HOTFIX,
    )


def opened(user: User, request: Request, minutes_ago: int) -> None:
    services.mark_seen(user, request)
    request.visits.filter(user=user).update(seen_at=timezone.now() - timedelta(minutes=minutes_ago))


def say(author: User, request: Request, text: str, minutes_ago: int) -> Message:
    message = services.post_message(author, request, text)
    Message.objects.filter(pk=message.pk).update(
        created_at=timezone.now() - timedelta(minutes=minutes_ago)
    )
    message.refresh_from_db()
    return message


def unread_on_my_requests(client, user: User) -> int:
    client.force_login(user)
    [item] = client.get(MY_REQUESTS).context["page"].requests
    return item.unread


def test_pm_reply_shows_as_new_on_my_requests(client, bug, anna, pm):
    opened(anna, bug, minutes_ago=30)
    say(pm, bug, "Could you share a lead link?", minutes_ago=10)

    assert unread_on_my_requests(client, anna) == 1
    assert "1 new" in client.get(MY_REQUESTS).content.decode()


def test_opening_the_card_clears_new(client, bug, anna, pm):
    opened(anna, bug, minutes_ago=30)
    say(pm, bug, "Could you share a lead link?", minutes_ago=10)
    client.force_login(anna)

    client.get(bug.get_absolute_url())

    assert unread_on_my_requests(client, anna) == 0


def test_own_messages_are_never_new(client, bug, anna):
    opened(anna, bug, minutes_ago=30)
    say(anna, bug, "One more detail", minutes_ago=10)

    assert unread_on_my_requests(client, anna) == 0


def test_colleague_message_is_new_to_the_client(client, bug, anna, mark):
    opened(anna, bug, minutes_ago=30)
    say(mark, bug, "Happens to me too", minutes_ago=10)

    assert unread_on_my_requests(client, anna) == 1


def test_one_persons_visit_does_not_clear_new_for_a_colleague(client, bug, anna, mark, pm):
    opened(anna, bug, minutes_ago=30)
    opened(mark, bug, minutes_ago=30)
    say(pm, bug, "Fixed on staging", minutes_ago=10)
    client.force_login(mark)
    client.get(bug.get_absolute_url())

    assert unread_on_my_requests(client, anna) == 1


def test_a_request_never_opened_counts_every_message_from_others(client, bug, mark, pm):
    say(pm, bug, "Thanks!", minutes_ago=20)
    say(pm, bug, "Planned for 2.15", minutes_ago=10)

    assert unread_on_my_requests(client, mark) == 2


def test_thread_marks_the_first_message_since_the_last_visit(client, bug, anna, pm):
    say(pm, bug, "Thanks!", minutes_ago=40)
    opened(anna, bug, minutes_ago=30)
    first_new = say(pm, bug, "Could you share a lead link?", minutes_ago=10)
    say(pm, bug, "Any luck?", minutes_ago=5)
    client.force_login(anna)

    response = client.get(bug.get_absolute_url())

    assert response.context["page"].first_new == first_new
    assert "2 new since you last opened it" in response.content.decode()


def test_pm_sees_the_client_reply_as_new_on_the_card(bug, anna, pm):
    opened(pm, bug, minutes_ago=30)
    reply = say(anna, bug, "Here is the lead link", minutes_ago=5)

    page = selectors.request_page(pm, bug.pk)

    assert (page.first_new, page.new_count) == (reply, 1)


def test_nothing_is_new_after_reading_everything(bug, anna, pm):
    say(pm, bug, "Thanks!", minutes_ago=40)
    opened(anna, bug, minutes_ago=30)

    page = selectors.request_page(anna, bug.pk)

    assert (page.first_new, page.new_count) == (None, 0)


def test_board_card_names_its_status_only_where_the_column_mixes_them(client, anna):
    received = services.create_request(
        anna, RequestType.BUG, "Waits in line", why="x", when_needed=WhenNeeded.NOT_SURE
    )
    building = services.create_request(
        anna, RequestType.BUG, "Being built", why="x", when_needed=WhenNeeded.NOT_SURE
    )
    Request.objects.filter(pk=building.pk).update(status=ClientStatus.IN_PROGRESS)
    client.force_login(anna)

    html = client.get(MY_REQUESTS, {"view": "board"}).content.decode()
    queued = html.split('data-column="queued"')[1].split('class="board-column"')[0]
    in_progress = html.split('data-column="in-progress"')[1].split('class="board-column"')[0]

    assert received.title in queued
    assert "status-received" in queued
    assert building.title in in_progress
    assert "status-in_progress" not in in_progress
