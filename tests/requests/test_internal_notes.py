import pytest
from django.urls import reverse

from apps.accounts.models import Company, User
from apps.requests import selectors, services
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded

pytestmark = pytest.mark.django_db

NOTE = "Olga thinks two days; check archived sources with her"


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm)


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna")


@pytest.fixture
def bug(anna) -> Request:
    return services.create_request(
        anna, RequestType.BUG, "Duplicate leads", why="Calls twice", when_needed=WhenNeeded.HOTFIX
    )


def write(client, user: User, request: Request, kind: str, text: str, **extra):
    client.force_login(user)
    return client.post(request.get_absolute_url(), {"kind": kind, "text": text, **extra})


def test_pm_note_is_never_shown_to_the_client(client, pm, anna, bug):
    write(client, pm, bug, "note", NOTE)

    client.force_login(anna)
    card = client.get(bug.get_absolute_url()).content.decode()
    board = client.get(reverse("my_requests")).content.decode()

    assert NOTE not in card
    assert "Internal note" not in card
    assert NOTE not in board


def test_pm_sees_the_note_in_the_thread(client, pm, bug):
    write(client, pm, bug, "note", NOTE)

    page = selectors.request_page(pm, bug.pk)

    assert [e.note.text for e in page.thread if e.note] == [NOTE]


def test_a_note_does_not_count_as_new_for_the_client(client, pm, anna, bug):
    client.force_login(anna)
    client.get(bug.get_absolute_url())

    write(client, pm, bug, "note", NOTE)

    page = selectors.request_page(anna, bug.pk)
    assert page.new_count == 0


def test_a_reply_still_reaches_the_client(client, pm, anna, bug):
    write(client, pm, bug, "reply", "Could you share a lead link?")

    client.force_login(anna)
    assert "Could you share a lead link?" in client.get(bug.get_absolute_url()).content.decode()


def test_client_cannot_write_a_note(client, anna, bug):
    response = write(client, anna, bug, "note", "Sneaky")

    assert response.status_code == 403
    assert not bug.notes.exists()


def test_a_note_takes_a_client_reply_out_of_needs_you(client, pm, anna, bug):
    Request.objects.filter(pk=bug.pk).update(status=ClientStatus.IN_PROGRESS)
    services.post_message(pm, bug, "In review")
    services.post_message(anna, bug, "Thanks!")

    write(client, pm, bug, "note", "Nothing to answer, waiting on QA")

    assert bug.pk not in selectors.pm_reasons(selectors.requests_for(pm))


def test_empty_note_is_refused_with_a_hint(client, pm, bug):
    response = write(client, pm, bug, "note", "  ")

    assert response.status_code == 422
    assert "Write the note." in response.content.decode()


def test_no_notes_in_a_closed_request(client, pm, bug):
    Request.objects.filter(pk=bug.pk).update(status=ClientStatus.DONE)

    response = write(client, pm, bug, "note", NOTE)

    assert response.status_code == 403
