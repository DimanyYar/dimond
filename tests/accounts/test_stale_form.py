import pytest
from django.conf import settings
from django.test import Client
from django.utils.crypto import get_random_string

from apps.accounts.models import Company, SignInLink, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def browser() -> Client:
    """A browser that checks form tokens like a real one; the test client skips that."""
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def atlas() -> Company:
    return Company.objects.create(name="Atlas")


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna.k@atlas-crm.com", name="Anna K.", company=atlas)


@pytest.fixture
def mark(atlas) -> User:
    return User.objects.create_user("mark@atlas-crm.com", name="Mark T.", company=atlas)


def form_token(response) -> str:
    return str(response.context["csrf_token"])


def in_another_tab(browser: Client, user: User | None) -> None:
    """Sign in (or out, with None) elsewhere in the same browser. Like a real sign-in, it
    gives the browser a new form token; the test client's own login skips that part."""
    if user is None:
        browser.logout()
    else:
        browser.force_login(user)
    browser.cookies[settings.CSRF_COOKIE_NAME] = get_random_string(32)


def test_resend_from_an_old_tab_explains_instead_of_a_django_error(browser, anna, mark):
    old_tab = form_token(browser.get("/sign-in/"))
    in_another_tab(browser, mark)

    response = browser.post("/sign-in/", {"email": anna.email, "csrfmiddlewaretoken": old_tab})

    page = response.content.decode()
    assert response.status_code == 403
    assert "This page is out of date" in page
    assert "Your last action didn’t go through." in page
    assert 'You’re now signed in as <strong class="ink">Mark T.</strong>' in page
    assert "CSRF" not in page
    assert not SignInLink.objects.exists()


def test_signed_out_person_is_offered_to_sign_in_again(browser, anna):
    browser.force_login(anna)
    old_tab = form_token(browser.get("/requests/"))
    in_another_tab(browser, None)

    response = browser.post("/sign-out/", {"csrfmiddlewaretoken": old_tab})

    assert response.status_code == 403
    assert 'href="/sign-in/"' in response.content.decode()


def test_sign_out_from_the_out_of_date_page_works(browser, anna, mark):
    browser.force_login(anna)
    old_tab = form_token(browser.get("/requests/"))
    in_another_tab(browser, mark)

    stale = browser.post("/sign-out/", {"csrfmiddlewaretoken": old_tab})
    response = browser.post("/sign-out/", {"csrfmiddlewaretoken": form_token(stale)})

    assert response.status_code == 302
    assert "_auth_user_id" not in browser.session
