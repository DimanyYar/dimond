import pytest

from apps.accounts.models import Company, Role, User

from .conftest import PNG

pytestmark = pytest.mark.django_db


def opened(client, attachment):
    return client.get(f"/files/{attachment.id}/")


def test_client_opens_a_file_of_own_company_request(client, screenshot, atlas):
    colleague = User.objects.create_user("mark@atlas-crm.com", name="Mark", company=atlas)
    client.force_login(colleague)

    response = opened(client, screenshot)

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PNG


def test_client_cannot_open_other_company_file(client, screenshot):
    northwind = Company.objects.create(name="Northwind")
    outsider = User.objects.create_user("lee@northwind.com", name="Lee", company=northwind)
    client.force_login(outsider)

    assert opened(client, screenshot).status_code == 404


def test_pm_opens_any_file(client, screenshot, pm):
    client.force_login(pm)

    assert opened(client, screenshot).status_code == 200


def test_team_cannot_open_files_until_requests_are_linked_to_jira(client, screenshot):
    developer = User.objects.create_user("olga@shakuro.com", name="Olga", role=Role.TEAM)
    client.force_login(developer)

    assert opened(client, screenshot).status_code == 404


def test_signed_out_person_is_sent_to_sign_in(client, screenshot):
    response = opened(client, screenshot)

    assert response.status_code == 302
    assert response["Location"].startswith("/sign-in/")


def test_client_without_access_cannot_open_files(client, screenshot, anna):
    client.force_login(anna)
    anna.is_active = False
    anna.save()

    assert opened(client, screenshot).status_code == 302


def test_image_opens_in_the_browser_with_its_checked_type(client, screenshot, anna):
    client.force_login(anna)

    response = opened(client, screenshot)

    assert response["Content-Type"] == "image/png"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Disposition"].startswith("inline")
    assert "private" in response["Cache-Control"]


def test_text_file_downloads_instead_of_opening(client, screenshot, anna):
    screenshot.name, screenshot.content_type = "export.log", "text/plain"
    screenshot.save()
    client.force_login(anna)

    response = opened(client, screenshot)

    assert response["Content-Disposition"] == 'attachment; filename="export.log"'


def test_request_card_lists_its_files(client, screenshot, anna):
    client.force_login(anna)

    page = client.get(screenshot.request.get_absolute_url()).content.decode()

    assert f'href="/files/{screenshot.id}/"' in page
    assert "duplicates.png" in page
