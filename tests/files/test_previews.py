from io import BytesIO

import pytest
from PIL import Image

from apps.accounts.models import Company, User
from apps.files.models import Attachment, PreviewStatus
from apps.files.services import make_pending_previews
from apps.files.uploads import check_upload
from apps.requests.services import create_request

from .conftest import M4A, MP4, PNG, upload

pytestmark = pytest.mark.django_db


def picture(format: str, size=(1600, 900), pages: int = 1, colour: str = "white") -> bytes:
    out = BytesIO()
    first, *rest = [Image.new("RGB", size, fill) for fill in (colour, "red", "blue")[:pages]]
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


def video_with_frame(client, anna, frame: bytes, frame_name: str = "0.jpg"):
    client.force_login(anna)
    client.post(
        "/requests/new/",
        {
            "type": "idea",
            "title": "Lead form sends twice",
            "why": "Managers call twice",
            "when_needed": "not_sure",
            "attachments": [upload("bug.mp4", MP4)],
            "attachments_posters": [upload(frame_name, frame)],
        },
    )
    return Attachment.objects.get()


def test_video_preview_is_the_frame_the_browser_took(client, anna, private_folder):
    video = video_with_frame(client, anna, picture("JPEG", size=(1280, 720)))
    assert video.preview_status == PreviewStatus.PENDING
    # Waiting for the worker, the frame is not shown: only the worker's WebP is.
    assert client.get(f"/files/{video.id}/preview/").status_code == 404

    make_pending_previews()

    video.refresh_from_db()
    assert video.preview_status == PreviewStatus.READY
    with video.preview.open("rb") as stored, Image.open(stored) as preview:
        assert (preview.format, preview.size) == ("WEBP", (320, 180))
    # The frame as sent is gone once the preview is made.
    assert [path.name for path in (private_folder / "previews").iterdir()] == [f"{video.id}.webp"]


@pytest.mark.parametrize(
    ("frame", "frame_name"),
    [
        (picture("PNG"), "0.jpg"),  # not a JPEG
        (picture("JPEG"), "1.jpg"),  # a frame of a second video that was not sent
    ],
    ids=["not-a-jpeg", "another-video"],
)
def test_video_without_its_own_jpeg_frame_keeps_the_play_button(client, anna, frame, frame_name):
    video = video_with_frame(client, anna, frame, frame_name)

    assert video.preview_status == PreviewStatus.NONE
    assert not video.preview
    card = client.get(video.request.get_absolute_url()).content.decode()
    assert 'class="tile-play"' in card


def test_two_videos_with_one_name_each_get_their_own_frame(client, anna):
    client.force_login(anna)
    red, blue = (picture("JPEG", size=(640, 360), colour=colour) for colour in ("red", "blue"))
    # Same name, different files: the second is a few bytes longer, which tells them apart.
    first, second = MP4, MP4 + b"\x00" * 16
    client.post(
        "/requests/new/",
        {
            "type": "idea",
            "title": "Lead form sends twice",
            "why": "Managers call twice",
            "when_needed": "not_sure",
            "attachments": [upload("clip.mp4", first), upload("clip.mp4", second)],
            "attachments_posters": [upload("0.jpg", red), upload("1.jpg", blue)],
        },
    )

    make_pending_previews()

    def is_red(video):
        with video.preview.open("rb") as stored, Image.open(stored) as preview:
            red, _, blue = preview.convert("RGB").getpixel((0, 0))
            return red > blue

    assert is_red(Attachment.objects.get(size=len(first)))
    assert not is_red(Attachment.objects.get(size=len(second)))


def test_broken_frame_leaves_the_video_without_preview(client, anna, private_folder):
    video = video_with_frame(client, anna, b"\xff\xd8\xff" + b"\x00" * 64)

    make_pending_previews()

    video.refresh_from_db()
    assert video.preview_status == PreviewStatus.NONE
    assert not (private_folder / "previews").exists() or not any(
        (private_folder / "previews").iterdir()
    )


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
