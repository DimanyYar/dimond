import pytest
from django.template.loader import render_to_string

from apps.accounts.models import Company, User
from apps.requests.models import RequestType, WhenNeeded
from apps.requests.services import create_request

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas_request():
    atlas = Company.objects.create(name="Atlas")
    anna = User.objects.create_user("anna@atlas.test", company=atlas, name="Anna")
    return create_request(
        anna, RequestType.BUG, "Duplicate leads", why="Calls twice", when_needed=WhenNeeded.HOTFIX
    )


@pytest.mark.parametrize("debug", [True, False])
def test_request_of_another_company_shows_a_friendly_not_found_page(
    client, settings, atlas_request, debug
):
    settings.DEBUG = debug
    northwind = Company.objects.create(name="Northwind")
    client.force_login(User.objects.create_user("lee@northwind.test", company=northwind))

    response = client.get(atlas_request.get_absolute_url())

    page = response.content.decode()
    assert response.status_code == 404
    assert "Nothing here" in page
    assert "Back to dimon.d" in page
    assert "URLconf" not in page


def test_unknown_address_shows_the_same_page(client, atlas_request):
    client.force_login(atlas_request.author)

    response = client.get("/no-such-page/")

    assert response.status_code == 404
    assert "Nothing here" in response.content.decode()


def test_signed_out_visitor_is_offered_to_sign_in(client):
    response = client.get("/no-such-page/")

    assert "Sign in" in response.content.decode()


def test_server_error_page_needs_nothing_from_the_request():
    assert "Something broke on our side" in render_to_string("500.html")
