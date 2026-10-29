import pytest

from apps.accounts.models import Company, Role, User
from apps.requests.models import FORM_TYPES, ClientStatus, Request, WhenNeeded

pytestmark = pytest.mark.django_db

URL = "/requests/new/"


@pytest.fixture
def ruslan(client, pm) -> User:
    atlas = Company.objects.create(name="Atlas", owner=pm)
    user = User.objects.create_user("ruslan.v@atlas-crm.com", name="Ruslan", company=atlas)
    client.force_login(user)
    return user


def filled(type: str, **fields: str) -> dict[str, str]:
    data = {
        "type": type,
        "title": "Leads export times out",
        "why": "Sales need the list every Monday",
        "when_needed": "this_release",
        "link": "https://crm.example.com/leads/12345",
    }
    data.update(fields)
    return data


@pytest.mark.parametrize("type", [value for value, _ in FORM_TYPES])
def test_client_sends_a_request_of_each_type(client, ruslan, type):
    response = client.post(URL, filled(type, when_needed="hotfix"), follow=True)

    request = Request.objects.get()
    assert (request.type, request.status, request.author) == (type, ClientStatus.RECEIVED, ruslan)
    assert request.when_needed == WhenNeeded.HOTFIX
    assert f"DMN-{request.pk} · Leads export times out" in response.content.decode()


def test_bug_without_a_link_is_not_sent(client, ruslan):
    response = client.post(URL, filled("bug", link=""))

    assert response.status_code == 422
    assert "Add a link to the lead or page" in response.content.decode()
    assert not Request.objects.exists()


@pytest.mark.parametrize("type", ["improvement", "feature", "idea"])
def test_link_is_optional_for_everything_but_bugs(client, ruslan, type):
    client.post(URL, filled(type, link=""))

    assert Request.objects.get().link == ""


@pytest.mark.parametrize("field", ["title", "why"])
def test_title_and_why_it_matters_are_required(client, ruslan, field):
    response = client.post(URL, filled("idea", **{field: ""}))

    assert response.status_code == 422
    assert not Request.objects.exists()


def test_title_with_line_breaks_is_saved_as_one_line(client, ruslan):
    # The page's input cannot hold a line break; a hand-made request can.
    client.post(URL, filled("bug", title="Leads export\r\n  times out"))

    assert Request.objects.get().title == "Leads export times out"


def test_typed_text_survives_a_bounced_form(client, ruslan):
    response = client.post(URL, filled("bug", link="", description="Gateway timeout"))

    page = response.content.decode()
    assert "Gateway timeout" in page
    assert 'value="Leads export times out"' in page


def test_steps_are_kept_only_for_bugs(client, ruslan):
    client.post(URL, filled("bug", steps="1. Export all leads"))
    client.post(URL, filled("idea", steps="left over from Bug"))

    assert dict(Request.objects.values_list("type", "steps")) == {
        "bug": "1. Export all leads",
        "idea": "",
    }


def test_new_form_starts_as_a_bug_with_urgency_not_sure(client, ruslan):
    page = client.get(URL).content.decode()

    assert '<input type="radio" name="type" value="bug" checked>' in page
    assert '<input type="radio" name="when_needed" value="not_sure" checked>' in page


@pytest.mark.parametrize("role", [Role.PM, Role.TEAM])
def test_only_clients_can_open_the_form(client, role):
    client.force_login(User.objects.create_user(f"{role}@shakuro.com", role=role))

    assert client.get(URL).status_code == 403
    assert client.post(URL, filled("idea")).status_code == 403


def test_revoked_client_is_sent_to_sign_in(client, ruslan):
    ruslan.is_active = False
    ruslan.save()

    response = client.get(URL)

    assert response.status_code == 302
    assert not Request.objects.exists()


def test_page_script_gets_the_address_instead_of_a_redirect(client, ruslan):
    response = client.post(URL, filled("idea"), headers={"X-Requested-With": "fetch"})

    assert (response.status_code, response["Location"]) == (201, "/requests/")
    assert "DMN-" in client.get("/requests/").content.decode()
