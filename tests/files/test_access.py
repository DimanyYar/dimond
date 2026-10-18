import pytest

from apps.accounts.models import Company, Role, User
from apps.files.uploads import check_upload
from apps.requests.services import create_request

from .conftest import MP4, PNG, upload

pytestmark = pytest.mark.django_db


def opened(client, attachment):
    return client.get(f"/files/{attachment.id}/")


def test_client_opens_a_file_of_own_company_request(client, screenshot, atlas):
    colleague = User.objects.create_user("mark@atlas-crm.com", name="Mark", company=atlas)
    client.force_login(colleague)

    response = opened(client, screenshot)

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PNG


def test_client_cannot_open_other_company_file(client, screenshot, pm):
    northwind = Company.objects.create(name="Northwind", owner=pm)
    outsider = User.objects.create_user("lee@northwind.com", name="Lee", company=northwind)
    client.force_login(outsider)

    assert opened(client, screenshot).status_code == 404


def test_pm_opens_a_file_of_their_client(client, screenshot, pm):
    client.force_login(pm)

    assert opened(client, screenshot).status_code == 200


def test_pm_cannot_open_a_file_of_another_pms_client(client, screenshot):
    ivan = User.objects.create_user("ivan@shakuro.com", name="Ivan", role=Role.PM)
    client.force_login(ivan)

    assert opened(client, screenshot).status_code == 404


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
    assert "no-store" in response["Cache-Control"]


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


def test_player_gets_the_piece_of_the_file_it_asks_for(client, screenshot, anna):
    client.force_login(anna)

    response = client.get(f"/files/{screenshot.id}/", headers={"Range": "bytes=8-15"})

    assert response.status_code == 206
    assert b"".join(response.streaming_content) == PNG[8:16]
    assert response["Content-Length"] == "8"
    assert response["Content-Range"] == f"bytes 8-15/{len(PNG)}"
    assert "no-store" in response["Cache-Control"]


@pytest.mark.parametrize(
    ("asked", "first", "last"),
    [("bytes=60-", 60, len(PNG) - 1), ("bytes=-10", len(PNG) - 10, len(PNG) - 1)],
)
def test_player_gets_the_rest_or_the_end_of_the_file(client, screenshot, anna, asked, first, last):
    client.force_login(anna)

    response = client.get(f"/files/{screenshot.id}/", headers={"Range": asked})

    assert b"".join(response.streaming_content) == PNG[first : last + 1]


def test_piece_beyond_the_end_of_the_file_is_refused(client, screenshot, anna):
    client.force_login(anna)

    response = client.get(f"/files/{screenshot.id}/", headers={"Range": "bytes=999-"})

    assert response.status_code == 416
    assert response["Content-Range"] == f"bytes */{len(PNG)}"


def test_pieces_of_a_file_need_the_same_access(client, screenshot, pm):
    northwind = Company.objects.create(name="Northwind", owner=pm)
    outsider = User.objects.create_user("lee@northwind.com", name="Lee", company=northwind)
    client.force_login(outsider)

    response = client.get(f"/files/{screenshot.id}/", headers={"Range": "bytes=0-7"})

    assert response.status_code == 404


def test_whole_file_tells_the_player_it_can_ask_for_pieces(client, screenshot, anna):
    client.force_login(anna)

    assert opened(client, screenshot)["Accept-Ranges"] == "bytes"


def test_long_piece_of_a_video_is_sent_in_parts(client, anna):
    video = MP4 + b"\x00" * 100_000
    request = create_request(
        anna,
        "bug",
        "Form sends twice",
        "Calls twice",
        "not_sure",
        files=[check_upload(upload("bug.mp4", video))],
    )
    client.force_login(anna)

    response = client.get(f"/files/{request.attachments.get().id}/", headers={"Range": "bytes=0-"})

    parts = list(response.streaming_content)
    assert len(parts) > 1
    assert b"".join(parts) == video
