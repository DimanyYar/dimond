import pytest
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.requests import selectors, services
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded
from apps.requests.selectors import PmReason

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas() -> Company:
    return Company.objects.create(name="Atlas")


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna")


@pytest.fixture
def pm() -> User:
    return User.objects.create_user("dmitry@shakuro.com", role=Role.PM, name="Dmitry")


def make(author: User, title: str = "Bulk SMS", status=ClientStatus.RECEIVED) -> Request:
    request = services.create_request(
        author, RequestType.IDEA, title, why="Context", when_needed=WhenNeeded.NOT_SURE
    )
    Request.objects.filter(pk=request.pk).update(status=status)
    request.refresh_from_db()
    return request


def triage(client, user: User, request: Request, action: str):
    client.force_login(user)
    return client.post(reverse("request_triage", args=[request.pk]), {"action": action})


def reason(pm: User, request: Request) -> PmReason | None:
    return selectors.pm_reasons(selectors.requests_for(pm)).get(request.pk)


def test_pm_moves_a_new_request_to_later_and_it_leaves_needs_you(client, pm, anna):
    idea = make(anna)

    triage(client, pm, idea, "later")

    idea.refresh_from_db()
    assert idea.status == ClientStatus.LATER
    assert reason(pm, idea) is None
    assert idea.status_events.last().actor == pm


def test_planned_request_cannot_be_moved_to_later_by_hand(client, pm, anna):
    planned = make(anna, status=ClientStatus.PLANNED)

    assert triage(client, pm, planned, "later").status_code == 403


def test_pm_brings_a_request_back_from_later(client, pm, anna):
    parked = make(anna, status=ClientStatus.LATER)

    triage(client, pm, parked, "back")

    parked.refresh_from_db()
    assert parked.status == ClientStatus.RECEIVED


def test_client_cannot_use_the_pm_actions(client, anna):
    idea = make(anna)

    assert triage(client, anna, idea, "later").status_code == 403


@pytest.mark.parametrize(
    "status", [ClientStatus.RECEIVED, ClientStatus.IN_PROGRESS, ClientStatus.LATER]
)
def test_pm_declines_an_open_request_and_the_client_reads_why(client, pm, anna, status):
    idea = make(anna, status=status)
    client.force_login(pm)

    client.post(reverse("request_decline", args=[idea.pk]), {"reason": "Not this year."})

    idea.refresh_from_db()
    assert idea.status == ClientStatus.DECLINED
    client.force_login(anna)
    assert "Not this year." in client.get(idea.get_absolute_url()).content.decode()


def test_decline_without_a_reason_is_refused_with_a_hint(client, pm, anna):
    idea = make(anna)
    client.force_login(pm)

    response = client.post(reverse("request_decline", args=[idea.pk]), {"reason": "  "})

    assert response.status_code == 422
    assert "Tell the client why" in response.content.decode()
    idea.refresh_from_db()
    assert idea.status == ClientStatus.RECEIVED


def test_closed_request_cannot_be_declined(client, pm, anna):
    done = make(anna, status=ClientStatus.DONE)
    client.force_login(pm)

    response = client.post(reverse("request_decline", args=[done.pk]), {"reason": "Late"})

    assert response.status_code == 403


def test_no_reply_needed_takes_a_client_reply_out_of_needs_you(client, pm, anna):
    building = make(anna, status=ClientStatus.IN_PROGRESS)
    services.post_message(pm, building, "In review")
    services.post_message(anna, building, "Thanks!")

    triage(client, pm, building, "handled")

    assert reason(pm, building) is None


def test_the_next_client_message_brings_the_request_back_to_needs_you(client, pm, anna):
    building = make(anna, status=ClientStatus.IN_PROGRESS)
    services.post_message(pm, building, "In review")
    services.post_message(anna, building, "Thanks!")
    triage(client, pm, building, "handled")

    services.post_message(anna, building, "One more thing: the export too?")

    assert reason(pm, building) == PmReason.REPLIED


def test_no_reply_needed_only_while_the_client_replied(client, pm, anna):
    fresh = make(anna)

    assert triage(client, pm, fresh, "handled").status_code == 403


def test_no_reply_needed_on_a_fresh_request_does_not_leave_it_as_new(client, pm, anna):
    fresh = make(anna)
    services.post_message(anna, fresh, "Forgot a detail: it is the Monday export")

    triage(client, pm, fresh, "handled")

    assert reason(pm, fresh) is None


def test_moving_to_later_also_handles_a_waiting_reply(client, pm, anna):
    fresh = make(anna)
    services.post_message(anna, fresh, "Any news?")

    triage(client, pm, fresh, "later")

    assert reason(pm, fresh) is None


def test_handled_covers_the_messages_seen_not_the_moment_of_the_click(pm, anna):
    building = make(anna, status=ClientStatus.IN_PROGRESS)
    services.post_message(pm, building, "In review")
    thanks = services.post_message(anna, building, "Thanks!")

    services.mark_handled(pm, building)

    building.refresh_from_db()
    # A reply landing right after "Thanks!" is newer than the mark, so it stays unhandled.
    assert building.pm_handled_at == thanks.created_at


def test_a_request_brought_back_from_later_is_not_new_again(client, pm, anna):
    parked = make(anna, status=ClientStatus.LATER)

    triage(client, pm, parked, "back")

    assert reason(pm, parked) is None
