import pytest
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.requests import selectors, services
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded

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


def edit_url(request: Request) -> str:
    return reverse("request_edit", args=[request.pk])


def card_url(request: Request) -> str:
    return reverse("request_detail", args=[request.pk])


def form_data(**changes: str) -> dict[str, str]:
    data = {
        "type": "bug",
        "title": "Duplicate leads from the web form",
        "why": "Managers call the same person twice.",
        "when_needed": "hotfix",
        "link": "https://crm.example.com/leads/1",
    }
    data.update(changes)
    return data


def set_status(request: Request, status: ClientStatus) -> None:
    Request.objects.filter(pk=request.pk).update(status=status)
    request.refresh_from_db()


def test_edit_form_opens_filled_with_the_request(client, bug, anna):
    client.force_login(anna)

    response = client.get(edit_url(bug))

    assert response.status_code == 200
    assert b'value="Duplicate leads from the web form"' in response.content
    assert b'value="https://crm.example.com/leads/1"' in response.content


def test_colleague_edits_the_request_and_goes_back_to_the_card(client, bug, mark):
    client.force_login(mark)

    response = client.post(edit_url(bug), form_data(title="Every web form lead is doubled"))

    bug.refresh_from_db()
    assert response.status_code == 302
    assert response["Location"] == f"{card_url(bug)}#request"
    assert bug.title == "Every web form lead is doubled"
    assert bug.revisions.get().editor == mark


def test_edit_keeps_the_old_text_in_the_history(bug, mark):
    services.edit_request(
        mark,
        bug,
        RequestType.BUG,
        "Every web form lead is doubled",
        "Managers call the same person twice.",
        WhenNeeded.THIS_RELEASE,
        link="https://crm.example.com/leads/1",
    )

    [edit] = selectors.request_page(mark, bug.pk).edits

    assert edit.editor == mark
    assert edit.changes == [
        ("Title", "Duplicate leads from the web form", "Every web form lead is doubled"),
        ("When needed", "Hotfix", "This release"),
    ]


def test_card_marks_an_edited_request_and_says_so_in_the_thread(client, bug, mark, pm):
    client.force_login(mark)
    client.post(edit_url(bug), form_data(title="Every web form lead is doubled"))

    client.force_login(pm)
    card = client.get(card_url(bug)).content.decode()

    assert "edited ·" in card
    assert "Mark T. edited the request" in card


def test_saving_without_changes_does_not_mark_it_edited(client, bug, anna):
    client.force_login(anna)

    client.post(edit_url(bug), form_data())

    assert not bug.revisions.exists()


def test_edit_while_needing_input_keeps_waiting_for_the_answer(client, bug, anna, pm):
    services.post_message(pm, bug, "Which lead?", ask_for_input=True)
    client.force_login(anna)

    form = client.get(edit_url(bug))
    client.post(edit_url(bug), form_data(link="https://crm.example.com/leads/48213"))

    bug.refresh_from_db()
    assert b"Editing doesn\xe2\x80\x99t answer it" in form.content
    assert bug.status == ClientStatus.NEEDS_INPUT


@pytest.mark.parametrize(
    "status",
    [
        ClientStatus.PLANNED,
        ClientStatus.IN_PROGRESS,
        ClientStatus.READY_TO_CHECK,
        ClientStatus.LATER,
        ClientStatus.DONE,
        ClientStatus.DECLINED,
    ],
)
def test_request_is_read_only_once_planned(client, bug, anna, status):
    set_status(bug, status)
    client.force_login(anna)

    assert b"Edit request" not in client.get(card_url(bug)).content
    assert client.get(edit_url(bug))["Location"] == card_url(bug)
    with pytest.raises(services.NotAllowedError):
        services.edit_request(
            anna, bug, RequestType.BUG, "New title", "Why", WhenNeeded.HOTFIX, link="https://a.b"
        )


def test_typed_changes_come_back_when_the_request_got_planned_meanwhile(client, bug, anna):
    client.force_login(anna)
    client.get(edit_url(bug))
    set_status(bug, ClientStatus.PLANNED)

    response = client.post(edit_url(bug), form_data(title="Every web form lead is doubled"))

    bug.refresh_from_db()
    assert response.status_code == 409
    assert b"your changes weren\xe2\x80\x99t saved" in response.content
    assert b'value="Every web form lead is doubled"' in response.content
    assert bug.title == "Duplicate leads from the web form"


def test_pm_cannot_edit_the_client_request(client, bug, pm):
    client.force_login(pm)

    assert b"Edit request" not in client.get(card_url(bug)).content
    assert client.post(edit_url(bug), form_data(title="PM wording")).status_code == 403
    assert not bug.revisions.exists()


def test_client_cannot_edit_other_company_request(client, bug):
    northwind = Company.objects.create(name="Northwind")
    client.force_login(User.objects.create_user("eve@northwind.com", company=northwind))

    assert client.get(edit_url(bug)).status_code == 404
    assert client.post(edit_url(bug), form_data(title="Hacked")).status_code == 404


def test_edit_form_shows_errors_like_the_new_request_form(client, bug, anna):
    client.force_login(anna)

    response = client.post(edit_url(bug), form_data(title="", link=""))

    assert response.status_code == 422
    assert b"Give it a title in one line." in response.content
    assert b"Add a link to the lead or page where it happens." in response.content


def test_status_progress_ignores_edits(bug, anna):
    services.edit_request(
        anna,
        bug,
        RequestType.BUG,
        "Every web form lead is doubled",
        "Managers call the same person twice.",
        WhenNeeded.HOTFIX,
        link="https://crm.example.com/leads/1",
    )

    page = selectors.request_page(anna, bug.pk)

    assert page.progress.since == bug.status_events.get().created_at
