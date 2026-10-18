import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.accounts.models import Company, Role, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", email_domain="atlas-crm.com", owner=pm)


def test_client_person_belongs_to_a_company(atlas):
    anna = User.objects.create_user("anna.k@atlas-crm.com", name="Anna K.", company=atlas)

    assert anna.role == Role.CLIENT
    assert list(atlas.people.all()) == [anna]


def test_client_cannot_exist_without_a_company():
    with pytest.raises(ValidationError):
        User.objects.create_user("lost@example.com", role=Role.CLIENT)


def test_team_member_and_pm_belong_to_no_company(atlas):
    with pytest.raises(ValidationError):
        User.objects.create_user("dev@shakuro.com", role=Role.TEAM, company=atlas)

    User.objects.create_user("qa@shakuro.com", role=Role.TEAM)


def test_database_rejects_client_without_company_even_bypassing_validation():
    with pytest.raises(IntegrityError):
        User.objects.bulk_create([User(email="raw@example.com", role=Role.CLIENT)])


def test_email_is_one_login_regardless_of_letter_case(atlas):
    User.objects.create_user("Anna.K@Atlas-CRM.com", company=atlas)

    assert User.objects.filter(email="anna.k@atlas-crm.com").exists()
    with pytest.raises(ValidationError):
        User.objects.create_user("ANNA.K@atlas-crm.com", company=atlas)


def test_database_rejects_mixed_case_email_even_bypassing_validation(atlas):
    with pytest.raises(IntegrityError):
        User.objects.bulk_create([User(email="Anna.K@atlas-crm.com", company=atlas)])


def test_person_saved_without_the_manager_still_has_no_password(atlas):
    mark = User(email="mark.t@atlas-crm.com", company=atlas)
    mark.save()

    assert not mark.has_usable_password()


def test_people_have_no_password_to_sign_in_with(atlas):
    anna = User.objects.create_user("anna.k@atlas-crm.com", company=atlas)

    assert not anna.has_usable_password()


def test_superuser_is_a_pm_who_can_open_the_admin():
    dmitry = User.objects.create_superuser(
        "dmitry@example.com", password="s3cret-pass", name="Dmitry"
    )

    assert dmitry.role == Role.PM
    assert dmitry.company is None
    assert dmitry.is_staff
    assert dmitry.check_password("s3cret-pass")


def test_company_with_people_cannot_be_deleted(atlas):
    User.objects.create_user("anna.k@atlas-crm.com", company=atlas)

    with pytest.raises(IntegrityError):
        atlas.delete()
