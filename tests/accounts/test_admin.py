import pytest
from django.conf import settings

from apps.accounts.models import Company, Role, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def pm_client(client):
    pm = User.objects.create_superuser("dmitry@example.com", password="s3cret-pass")
    client.force_login(pm)
    return client


def test_pm_adds_a_client_person_in_the_admin(pm_client):
    atlas = Company.objects.create(name="Atlas")

    response = pm_client.post(
        f"/{settings.ADMIN_URL}accounts/user/add/",
        {
            "email": "Mark.T@atlas-crm.com",
            "name": "Mark T.",
            "role": Role.CLIENT,
            "company": atlas.pk,
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    mark = User.objects.get(email="mark.t@atlas-crm.com")
    assert mark.company == atlas
    assert not mark.has_usable_password()


def test_admin_is_not_at_the_default_address(pm_client):
    assert pm_client.get("/admin/").status_code == 404
