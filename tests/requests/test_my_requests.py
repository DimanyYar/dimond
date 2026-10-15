from datetime import UTC, datetime

import pytest
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.requests import selectors, services
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded
from apps.requests.selectors import Period, Show

pytestmark = pytest.mark.django_db

URL = reverse("my_requests")


@pytest.fixture
def atlas() -> Company:
    return Company.objects.create(name="Atlas")


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas-crm.com", company=atlas, name="Anna")


@pytest.fixture
def ruslan(atlas) -> User:
    return User.objects.create_user("ruslan.v@atlas-crm.com", company=atlas)


def make(
    author: User, title: str, *, type=RequestType.BUG, status=ClientStatus.RECEIVED, created=None
):
    request = services.create_request(
        author, type, title, why="Context", when_needed=WhenNeeded.NOT_SURE
    )
    Request.objects.filter(pk=request.pk).update(
        status=status, **({"created_at": created} if created else {})
    )
    return request


def titles(response) -> list[str]:
    return [r.title for r in response.context["page"].requests]


def test_client_lands_on_my_requests_after_sign_in(client, anna):
    client.force_login(anna)

    assert client.get("/").url == URL


def test_pm_keeps_the_placeholder_home_until_the_inbox_exists(client):
    client.force_login(User.objects.create_user("dmitry@shakuro.com", role=Role.PM))

    assert client.get("/").status_code == 200
    assert client.get(URL).status_code == 403


def test_client_sees_requests_from_everyone_at_their_company(client, anna, ruslan):
    make(ruslan, "Lead page is blank")
    northwind = Company.objects.create(name="Northwind")
    make(User.objects.create_user("lee@northwind.com", company=northwind), "Northwind bug")
    client.force_login(anna)

    assert titles(client.get(URL)) == ["Lead page is blank"]


def test_open_hides_done_and_declined_but_keeps_later(client, anna):
    make(anna, "Parked", status=ClientStatus.LATER)
    make(anna, "Shipped", status=ClientStatus.DONE)
    make(anna, "Not planned", status=ClientStatus.DECLINED)
    client.force_login(anna)

    assert titles(client.get(URL)) == ["Parked"]
    assert titles(client.get(URL, {"show": "closed"})) == ["Not planned", "Shipped"]
    assert len(titles(client.get(URL, {"show": "all"}))) == 3


def test_type_filter_shows_one_type(client, anna):
    make(anna, "A bug")
    make(anna, "An idea", type=RequestType.IDEA)
    client.force_login(anna)

    assert titles(client.get(URL, {"type": "idea"})) == ["An idea"]


def test_period_counts_requests_created_in_it(client, anna):
    make(anna, "Old one", created=datetime(2020, 1, 6, tzinfo=UTC))
    make(anna, "Fresh one")
    client.force_login(anna)

    assert titles(client.get(URL)) == ["Fresh one"]
    assert titles(client.get(URL, {"period": "all"})) == ["Fresh one", "Old one"]


def test_unknown_filter_values_fall_back_to_defaults(client, anna):
    make(anna, "A bug")
    client.force_login(anna)

    response = client.get(URL, {"show": "nope", "type": "nope", "period": "nope", "view": "nope"})

    assert response.status_code == 200
    assert response.context["show"] == Show.OPEN
    assert titles(response) == ["A bug"]


def test_summary_counts_the_period_by_client_status(anna):
    for status in [
        ClientStatus.RECEIVED,
        ClientStatus.NEEDS_INPUT,
        ClientStatus.IN_PROGRESS,
        ClientStatus.LATER,
        ClientStatus.DONE,
        ClientStatus.DECLINED,
    ]:
        make(anna, status.label, status=status)

    summary = selectors.period_summary(anna, since=None)

    assert summary == {
        "received": 6,
        "in_work": 2,
        "later": 1,
        "done": 1,
        "declined": 1,
        "closed": 2,
        "open": 4,
    }


@pytest.mark.parametrize(
    ("period", "start"),
    [
        (Period.WEEK, datetime(2026, 10, 12, tzinfo=UTC)),
        (Period.MONTH, datetime(2026, 10, 1, tzinfo=UTC)),
        (Period.THREE_MONTHS, datetime(2026, 8, 1, tzinfo=UTC)),
        (Period.ALL, None),
    ],
)
def test_week_starts_on_monday_and_three_months_on_a_month_start(period, start):
    thursday = datetime(2026, 10, 15, 22, 6, tzinfo=UTC)

    assert selectors.period_start(period, thursday) == start


def test_three_months_reach_into_last_year_in_january():
    assert selectors.period_start(Period.THREE_MONTHS, datetime(2027, 1, 20, tzinfo=UTC)) == (
        datetime(2026, 11, 1, tzinfo=UTC)
    )


def test_board_groups_statuses_into_columns_and_skips_empty_ones(anna):
    make(anna, "Waiting", status=ClientStatus.RECEIVED)
    make(anna, "Scheduled", status=ClientStatus.PLANNED)
    make(anna, "Question", status=ClientStatus.NEEDS_INPUT)

    columns = selectors.board(selectors.requests_for(anna))

    assert [(column.name, [r.title for r in items]) for column, items in columns] == [
        ("Needs you", ["Question"]),
        ("Queued", ["Scheduled", "Waiting"]),
    ]


def test_list_view_groups_requests_by_month(client, anna):
    make(anna, "September one", created=datetime(2026, 9, 3, tzinfo=UTC))
    make(anna, "August one", created=datetime(2026, 8, 20, tzinfo=UTC))
    client.force_login(anna)

    page = client.get(URL, {"view": "list", "period": "all"}).content.decode()

    assert page.index("September 2026") < page.index("September one") < page.index("August 2026")


def test_first_visit_invites_to_send_a_request(client, anna):
    client.force_login(anna)

    assert "No requests yet." in client.get(URL).content.decode()
