import pytest
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.accounts.models import Company, Role, User
from apps.tracker.base import TrackerUnreachable

pytestmark = pytest.mark.django_db


@pytest.fixture
def dmitry() -> User:
    return User.objects.create_superuser("dmitry@example.com", password="s3cret-pass")


@pytest.fixture
def pm_client(client, dmitry):
    client.force_login(dmitry)
    return client


def test_pm_adds_a_client_person_in_the_admin(pm_client, dmitry):
    atlas = Company.objects.create(name="Atlas", owner=dmitry)

    response = pm_client.post(
        f"/{settings.ADMIN_URL}accounts/user/add/",
        {
            "email": "Mark.T@atlas-crm.com",
            "name": "Mark T.",
            "role": Role.CLIENT,
            "company": atlas.pk,
            "notify": "own",
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    mark = User.objects.get(email="mark.t@atlas-crm.com")
    assert mark.company == atlas
    assert not mark.has_usable_password()


def test_admin_is_not_at_the_default_address(pm_client):
    assert pm_client.get("/admin/").status_code == 404


def test_admin_sets_the_jira_project_of_an_existing_client(pm_client, dmitry):
    atlas = Company.objects.create(name="Atlas", owner=dmitry)

    pm_client.post(
        f"/{settings.ADMIN_URL}accounts/company/{atlas.pk}/change/",
        {
            "name": "Atlas",
            "owner": dmitry.pk,
            "email_domain": "",
            "color": "#ff5600",
            "jira_project": "FI",
            "people-TOTAL_FORMS": "0",
            "people-INITIAL_FORMS": "0",
        },
    )

    atlas.refresh_from_db()
    assert atlas.jira_project == "FI"


def test_a_jira_project_key_must_look_like_one(dmitry):
    atlas = Company(name="Atlas", owner=dmitry, jira_project="fi; drop")

    with pytest.raises(ValidationError, match="A Jira project key, like FI."):
        atlas.full_clean()


def test_admin_offers_the_projects_the_tracker_sees(pm_client, dmitry):
    atlas = Company.objects.create(name="Atlas", owner=dmitry)

    page = pm_client.get(f"/{settings.ADMIN_URL}accounts/company/{atlas.pk}/change/").content
    assert '<option value="FI">FI · Atlas</option>' in page.decode()


def test_admin_takes_a_typed_key_when_the_tracker_is_down(pm_client, dmitry, monkeypatch):
    def jira_down():
        raise TrackerUnreachable("Jira at https://j.shakuro.com did not answer.")

    monkeypatch.setattr("apps.accounts.forms.current_tracker", jira_down)
    atlas = Company.objects.create(name="Atlas", owner=dmitry)

    page = pm_client.get(f"/{settings.ADMIN_URL}accounts/company/{atlas.pk}/change/").content
    assert "Type the key by hand" in page.decode()
