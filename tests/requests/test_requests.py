import pytest
from django.db.models import ProtectedError

from apps.accounts.models import Company, Role, User
from apps.requests import selectors, services
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas() -> Company:
    return Company.objects.create(name="Atlas")


@pytest.fixture
def ruslan(atlas) -> User:
    return User.objects.create_user("ruslan.v@atlas-crm.com", company=atlas)


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas-crm.com", company=atlas)


@pytest.fixture
def northwind_client() -> User:
    northwind = Company.objects.create(name="Northwind")
    return User.objects.create_user("lee@northwind.com", company=northwind)


@pytest.fixture
def pm() -> User:
    return User.objects.create_user("dmitry@shakuro.com", role=Role.PM)


@pytest.fixture
def developer() -> User:
    return User.objects.create_user("dev@shakuro.com", role=Role.TEAM)


@pytest.fixture
def bug(ruslan) -> Request:
    return services.create_request(
        ruslan,
        RequestType.BUG,
        "Lead page is blank",
        why="Sales can't call leads",
        when_needed=WhenNeeded.THIS_RELEASE,
    )


def test_new_request_is_received_and_belongs_to_the_author_company(bug, ruslan, atlas):
    assert bug.status == ClientStatus.RECEIVED
    assert bug.company == atlas
    first = bug.status_events.get()
    assert (first.from_status, first.to_status, first.actor) == ("", "received", ruslan)


def test_only_clients_can_create_requests(pm):
    with pytest.raises(services.NotAllowedError):
        services.create_request(
            pm, RequestType.IDEA, "Dark mode", why="Night shifts", when_needed=WhenNeeded.NOT_SURE
        )


def test_client_cannot_open_other_company_request(bug, northwind_client):
    with pytest.raises(Request.DoesNotExist):
        selectors.request_for(northwind_client, bug.pk)
    assert not selectors.requests_for(northwind_client).exists()


def test_colleague_sees_every_request_of_their_company(bug, anna):
    assert selectors.request_for(anna, bug.pk) == bug


def test_pm_sees_requests_of_all_companies(bug, northwind_client, pm):
    other = services.create_request(
        northwind_client,
        RequestType.FEATURE,
        "Export to CSV",
        why="Monthly report",
        when_needed=WhenNeeded.NEXT_RELEASE,
    )
    assert set(selectors.requests_for(pm)) == {bug, other}


def test_team_and_revoked_people_see_no_requests(bug, developer, anna):
    anna.is_active = False
    anna.save()
    assert not selectors.requests_for(developer).exists()
    assert not selectors.requests_for(anna).exists()


def test_status_change_is_logged_with_who_and_from_what(bug, pm):
    services.change_status(bug, ClientStatus.NEEDS_INPUT, actor=pm)
    services.change_status(bug, ClientStatus.PLANNED, actor=None)

    bug.refresh_from_db()
    assert bug.status == ClientStatus.PLANNED
    log = [(e.from_status, e.to_status, e.actor) for e in bug.status_events.all()]
    assert log[1:] == [("received", "needs_input", pm), ("needs_input", "planned", None)]


def test_same_status_again_is_not_logged(bug):
    services.change_status(bug, ClientStatus.RECEIVED, actor=None)
    assert bug.status_events.count() == 1


def test_client_cannot_change_status(bug, ruslan):
    with pytest.raises(services.NotAllowedError):
        services.change_status(bug, ClientStatus.DONE, actor=ruslan)
    bug.refresh_from_db()
    assert bug.status == ClientStatus.RECEIVED


def test_request_with_status_history_cannot_be_deleted(bug):
    with pytest.raises(ProtectedError):
        bug.delete()
    assert bug.status_events.exists()
