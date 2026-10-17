from io import BytesIO

import pytest
from PIL import Image

from apps.accounts.models import Company, User
from apps.files.models import PreviewStatus
from apps.files.services import make_pending_previews
from apps.files.uploads import check_upload
from apps.requests.services import create_request

from .conftest import M4A, PNG, upload

pytestmark = pytest.mark.django_db


def picture(format: str, size=(1600, 900), pages: int = 1) -> bytes:
    out = BytesIO()
    first, *rest = [Image.new("RGB", size, colour) for colour in ("white", "red", "blue")[:pages]]
    first.save(out, format, save_all=pages > 1, append_images=rest)
    return out.getvalue()


def sent(anna, *files):
    request = create_request(
        anna,
        "bug",
        "Leads export times out",
        "Sales need it",
        "not_sure",
        link="https://crm.example.com/leads/1",
        files=[check_upload(upload(name, content)) for name, content in files],
    )
    return request.attachments.get() if len(files) == 1 else request


def test_screenshot_gets_a_small_preview(anna):
    screenshot = sent(anna, ("duplicates.png", picture("PNG")))
    assert screenshot.preview_status == PreviewStatus.PENDING

    assert make_pending_previews() == 1

    screenshot.refresh_from_db()
    assert screenshot.preview_status == PreviewStatus.READY
    with screenshot.preview.open("rb") as stored, Image.open(stored) as preview:
        assert preview.format == "WEBP"
        assert preview.size == (320, 180)


def test_pdf_preview_is_its_first_page_and_counts_pages(anna):
    spec = sent(anna, ("spec.pdf", picture("PDF", size=(600, 800), pages=3)))

    make_pending_previews()

    spec.refresh_from_db()
    assert (spec.preview_status, spec.pages) == (PreviewStatus.READY, 3)


def test_broken_picture_keeps_a_tile_without_preview_and_still_opens(client, anna):
    broken = sent(anna, ("broken.png", PNG))

    make_pending_previews()

    broken.refresh_from_db()
    assert broken.preview_status == PreviewStatus.NONE
    client.force_login(anna)
    assert client.get(broken.get_absolute_url()).status_code == 200


def test_picture_out_of_reach_waits_for_next_run_and_others_still_get_previews(anna):
    request = sent(anna, ("first.png", picture("PNG")), ("second.png", picture("PNG")))
    first, second = request.attachments.order_by("name")
    first.file.storage.delete(first.file.name)

    make_pending_previews()

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.preview_status == PreviewStatus.PENDING
    assert second.preview_status == PreviewStatus.READY


def test_text_file_shows_its_first_lines(anna):
    log = sent(anna, ("export.log", "\n".join(f"10:4{n} line {n}" for n in range(9)).encode()))

    assert log.excerpt.splitlines() == [f"10:4{n} line {n}" for n in range(6)]
    assert log.preview_status == PreviewStatus.NONE


def test_sound_needs_no_preview(anna):
    assert sent(anna, ("note.m4a", M4A)).preview_status == PreviewStatus.NONE


def test_preview_opens_only_for_people_who_may_open_the_file(client, anna):
    screenshot = sent(anna, ("duplicates.png", picture("PNG")))
    make_pending_previews()
    url = f"/files/{screenshot.id}/preview/"

    client.force_login(anna)
    response = client.get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/webp"
    assert "no-store" in response["Cache-Control"]

    northwind = Company.objects.create(name="Northwind")
    client.force_login(User.objects.create_user("lee@northwind.com", name="Lee", company=northwind))
    assert client.get(url).status_code == 404


def test_preview_not_made_yet_is_not_found(client, anna):
    screenshot = sent(anna, ("duplicates.png", picture("PNG")))
    client.force_login(anna)

    assert client.get(f"/files/{screenshot.id}/preview/").status_code == 404


def test_card_shows_previews_as_tiles(client, anna):
    request = sent(anna, ("duplicates.png", picture("PNG")), ("spec.pdf", picture("PDF", pages=2)))
    client.force_login(anna)

    before = client.get(request.get_absolute_url()).content.decode()
    make_pending_previews()
    after = client.get(request.get_absolute_url()).content.decode()

    assert "preview soon" in before
    shot, spec = request.attachments.order_by("name")
    assert f'src="/files/{shot.id}/preview/"' in after
    assert "2 pages" in after
