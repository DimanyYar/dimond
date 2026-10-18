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


def withdraw_url(request: Request) -> str:
    return reverse("request_withdraw", args=[request.pk])


def decision_url(request: Request) -> str:
    return reverse("request_withdrawal_decision", args=[request.pk])


def card_url(request: Request) -> str:
    return reverse("request_detail", args=[request.pk])


def set_status(request: Request, status: ClientStatus) -> None:
    Request.objects.filter(pk=request.pk).update(status=status)
    request.refresh_from_db()


@pytest.fixture
def asked(bug, mark) -> Request:
    """A planned request that Mark asked to withdraw."""
    set_status(bug, ClientStatus.IN_PROGRESS)
    services.withdraw_request(mark, bug, "We switched the form to another vendor.")
    return bug


@pytest.mark.parametrize("status", [ClientStatus.RECEIVED, ClientStatus.NEEDS_INPUT])
def test_colleague_withdraws_an_unplanned_request_at_once(client, bug, mark, status):
    set_status(bug, status)
    client.force_login(mark)

    response = client.post(withdraw_url(bug), {"reason": ""})

    bug.refresh_from_db()
    assert response["Location"] == card_url(bug)
    assert bug.status == ClientStatus.WITHDRAWN
    assert bug.status_events.last().actor == mark
    assert not bug.messages.exists()


def test_reason_of_an_instant_withdrawal_goes_into_the_thread(bug, anna):
    services.withdraw_request(anna, bug, "Found a workaround.")

    thread = selectors.request_page(anna, bug.pk).thread
    lines = [e.message.text if e.message else e.event for e in thread]

    assert lines[-2:] == ["Found a workaround.", "Withdrawn"]


def test_withdrawn_request_is_closed_and_on_the_done_column(client, bug, anna):
    services.withdraw_request(anna, bug)
    client.force_login(anna)

    card = client.get(card_url(bug)).content.decode()
    board = client.get(reverse("my_requests"), {"show": "closed"}).context["page"].board

    assert "This request is closed." in card
    assert "Withdrawn by the client." in card
    assert [g.column.slug for g in board if bug in g.requests] == ["done"]


@pytest.mark.parametrize(
    "status",
    [
        ClientStatus.PLANNED,
        ClientStatus.IN_PROGRESS,
        ClientStatus.READY_TO_CHECK,
        ClientStatus.LATER,
    ],
)
def test_after_planning_the_client_only_asks_to_withdraw(client, bug, mark, status):
    set_status(bug, status)
    client.force_login(mark)

    client.post(withdraw_url(bug), {"reason": "We switched the form to another vendor."})

    bug.refresh_from_db()
    ask = selectors.request_page(mark, bug.pk).withdrawal
    assert bug.status == status
    assert (ask.requested_by, ask.reason.text) == (mark, "We switched the form to another vendor.")
    assert "Asked to withdraw · waiting for Dimon" in client.get(card_url(bug)).content.decode()


def test_asking_to_withdraw_needs_a_reason(client, bug, anna):
    set_status(bug, ClientStatus.PLANNED)
    client.force_login(anna)

    response = client.post(withdraw_url(bug), {"reason": "  "})

    assert response.status_code == 422
    assert "Tell Dimon why" in response.content.decode()
    assert not bug.withdrawal_requests.exists()


def test_client_cannot_ask_twice_while_dimon_decides(asked, anna):
    assert not selectors.can_ask_to_withdraw(anna, asked)
    with pytest.raises(services.NotAllowedError):
        services.withdraw_request(anna, asked, "Again")


def test_pm_sees_the_reason_and_confirms(client, asked, pm):
    client.force_login(pm)

    card = client.get(card_url(asked)).content.decode()
    client.post(decision_url(asked), {"decision": "confirm"})

    asked.refresh_from_db()
    assert "We switched the form to another vendor." in card
    assert "close it there yourself" in card
    assert asked.status == ClientStatus.WITHDRAWN
    assert asked.status_events.last().actor == pm


def test_pm_keeps_the_request_going(client, asked, pm, anna):
    client.force_login(pm)

    client.post(decision_url(asked), {"decision": "keep"})

    asked.refresh_from_db()
    page = selectors.request_page(anna, asked.pk)
    assert asked.status == ClientStatus.IN_PROGRESS
    assert page.withdrawal is None
    assert page.can_ask_to_withdraw
    assert "Dimon kept the request going" in [e.event for e in page.thread]


def test_client_cannot_decide_their_own_ask(client, asked, anna):
    client.force_login(anna)

    assert client.post(decision_url(asked), {"decision": "confirm"}).status_code == 403
    asked.refresh_from_db()
    assert asked.status == ClientStatus.IN_PROGRESS


def test_pm_cannot_withdraw_for_the_client(client, bug, pm):
    client.force_login(pm)

    assert client.post(withdraw_url(bug), {"reason": "Not needed"}).status_code == 403
    bug.refresh_from_db()
    assert bug.status == ClientStatus.RECEIVED


def test_client_cannot_withdraw_other_company_request(client, bug):
    northwind = Company.objects.create(name="Northwind")
    client.force_login(User.objects.create_user("eve@northwind.com", company=northwind))

    assert client.post(withdraw_url(bug), {"reason": "x"}).status_code == 404


@pytest.mark.parametrize("status", [ClientStatus.DONE, ClientStatus.DECLINED])
def test_closed_request_cannot_be_withdrawn(client, bug, anna, status):
    set_status(bug, status)
    client.force_login(anna)

    assert b"Withdraw request" not in client.get(card_url(bug)).content
    assert client.post(withdraw_url(bug), {"reason": "x"}).status_code == 403


def test_ask_left_open_is_dropped_when_the_sync_closes_the_request(asked, pm):
    services.change_status(asked, ClientStatus.DONE, None)

    assert selectors.request_page(pm, asked.pk).withdrawal is None
    with pytest.raises(services.NotAllowedError):
        services.decide_withdrawal(pm, asked, confirm=True)


def test_pm_finds_asks_to_withdraw_in_the_admin(client, asked, bug, anna):
    other = services.create_request(
        anna, RequestType.IDEA, "Dark theme", why="Eyes.", when_needed=WhenNeeded.NOT_SURE
    )
    client.force_login(User.objects.create_superuser("root@example.com", password="s3cret-pass"))
    admin_list = reverse("admin:requests_request_changelist")

    shown = client.get(admin_list, {"asks_to_withdraw": "yes"}).context["cl"].result_list

    assert list(shown) == [asked]
    assert other not in shown


def test_pm_reads_the_whole_reason_before_deciding(client, bug, mark, pm):
    set_status(bug, ClientStatus.PLANNED)
    reason = " ".join(["word"] * 80) + " and the last detail matters."
    services.withdraw_request(mark, bug, reason)
    client.force_login(pm)

    card = client.get(card_url(bug)).content.decode()

    assert f"<blockquote>{reason}</blockquote>" in card
