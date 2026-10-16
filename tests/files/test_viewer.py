import pytest

from apps.files.uploads import check_upload
from apps.requests import services

from .conftest import PDF, upload

pytestmark = pytest.mark.django_db


def test_viewer_goes_through_the_cards_images_and_pdfs(client, screenshot, anna):
    request = screenshot.request
    services.post_message(
        anna,
        request,
        "More",
        files=[check_upload(upload("spec.pdf", PDF)), check_upload(upload("export.log", b"ok"))],
    )
    client.force_login(anna)

    page = client.get(request.get_absolute_url()).content.decode()

    viewer = page[page.index('id="media-viewer"') :]
    assert 'data-name="duplicates.png"' in viewer
    assert 'data-name="spec.pdf"' in viewer
    # Text files download instead: there is nothing to look at in the viewer.
    assert 'data-name="export.log"' not in viewer


def test_card_without_images_or_pdfs_has_no_viewer(client, anna):
    request = services.create_request(anna, "idea", "Quiet leads", "We lose deals", "not_sure")
    client.force_login(anna)

    page = client.get(request.get_absolute_url()).content.decode()

    assert "media-viewer" not in page


def test_download_button_downloads_even_an_image(client, screenshot, anna):
    client.force_login(anna)

    response = client.get(f"/files/{screenshot.id}/?download")

    assert response["Content-Disposition"] == 'attachment; filename="duplicates.png"'


def test_only_the_site_itself_can_show_a_file_in_a_frame(client, screenshot, anna):
    client.force_login(anna)

    assert client.get(f"/files/{screenshot.id}/")["X-Frame-Options"] == "SAMEORIGIN"
    assert client.get(screenshot.request.get_absolute_url())["X-Frame-Options"] == "DENY"
