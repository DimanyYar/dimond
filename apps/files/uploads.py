"""What a file is, judged by its content, and whether dimond accepts it (PRD: access and
security, attachments).

A file's name says nothing reliable: an HTML page renamed to "photo.png" is still a page that
can run scripts. So the type comes from the first bytes, which every format starts with.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile

MAX_FILES = 5
MAX_SIZE = 10 * 1024 * 1024
# Two minutes of 720p video, which is what the browser will compress a clip to (PRD: access and
# security, attachments).
MAX_VIDEO_SIZE = 20 * 1024 * 1024

# The first bytes each accepted format starts with.
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
    (b"ID3", "audio/mpeg"),
    # An MP3 without a tag starts with its first frame: MPEG-1 or MPEG-2, with or without a
    # checksum (CRC) after the frame header.
    (b"\xff\xfb", "audio/mpeg"),
    (b"\xff\xfa", "audio/mpeg"),
    (b"\xff\xf3", "audio/mpeg"),
    (b"\xff\xf2", "audio/mpeg"),
    (b"OggS", "audio/ogg"),
)
# MP4, MOV and M4A start with a "ftyp" box after its 4-byte size, and WebM with the Matroska
# header. Both hold either picture and sound or sound alone, so the tracks decide which it is.
_MP4_BOX = b"ftyp"
_MATROSKA = b"\x1a\x45\xdf\xa3"
# A track's handler box ("hdlr", then 8 bytes of version and padding) names a picture track "vide".
_MP4_VIDEO_TRACK = re.compile(rb"hdlr.{8}vide", re.DOTALL)
# A Matroska track's codec id (element 0x86, then its one-byte length) starts with "V_" for picture.
_WEBM_VIDEO_TRACK = re.compile(rb"\x86[\x81-\xff]V_")
_WEBM_DOC_TYPE = b"\x42\x82\x84webm"
# RIFF files name their format in bytes 8 to 12.
_RIFF_FORMATS = {b"WEBP": "image/webp", b"WAVE": "audio/wav"}
# Word, Excel and PowerPoint files are zip archives (or, before 2007, OLE containers). Told
# apart from the rest only to explain why they are refused.
_OFFICE_AND_ARCHIVES = (b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"Rar!", b"7z\xbc\xaf")
# Text that a browser would treat as a page or a picture with scripts, not as plain text.
_MARKUP_STARTS = ("<!doctype", "<html", "<svg", "<?xml", "<script", "<head", "<body")
# A text file is downloaded under this name ending, never as ".bat" or ".command": Windows and
# macOS run those on a double click, even though their content is plain text.
_TEXT_ENDINGS = (".txt", ".log", ".csv")


class CheckedFile(NamedTuple):
    """An upload that passed `check_upload`; only these reach `services.attach_files`."""

    file: UploadedFile[Any]
    name: str
    content_type: str
    size: int


def _media_type(head: bytes, content: bytes) -> str | None:
    """The type of an MP4 or Matroska file."""
    if head[4:8] == _MP4_BOX:
        if _MP4_VIDEO_TRACK.search(content) is None:
            return "audio/mp4"
        # QuickTime ("qt  ") is what an iPhone records; Safari plays it, Chrome mostly does too.
        return "video/quicktime" if head[8:12] == b"qt  " else "video/mp4"
    # Plain Matroska (.mkv) is not WebM: Safari cannot play it, so it is refused.
    if _WEBM_DOC_TYPE not in content[:64]:
        return None
    return "video/webm" if _WEBM_VIDEO_TRACK.search(content) else "audio/webm"


def _content_type(head: bytes, content: bytes) -> str | None:
    for signature, content_type in _SIGNATURES:
        if head.startswith(signature):
            return content_type
    if head[:4] == b"RIFF":
        return _RIFF_FORMATS.get(head[8:12])
    if head[4:8] == _MP4_BOX or head.startswith(_MATROSKA):
        return _media_type(head, content)
    if b"\x00" in content:
        return None
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    # "#!" starts a script (shell, Python): a program, not a note.
    if text.startswith("#!") or text.lstrip().lower().startswith(_MARKUP_STARTS):
        return None
    return "text/plain"


def _too_big(name: str, is_video: bool) -> ValidationError:
    if is_video:
        return ValidationError(
            f"{name} is over 20 MB. Share a link to Google Drive, Loom or YouTube instead."
        )
    return ValidationError(f"{name} is over 10 MB. Make it smaller or share a link instead.")


def check_upload(upload: UploadedFile[Any]) -> CheckedFile:
    """Accept images, video, audio, PDF and plain text, 10 MB each and 20 MB for video; raise
    ValidationError with a message for the client otherwise."""
    name = (upload.name or "file")[-255:]
    if not upload.size:
        raise ValidationError(f"{name} is empty.")
    upload.seek(0)
    head = upload.read(16)
    # Too big for any file: refused before the rest is read into memory. An MP4 or WebM this
    # big is almost always a recorded clip, so it gets the hint about video.
    if upload.size > MAX_VIDEO_SIZE:
        raise _too_big(name, is_video=head[4:8] == _MP4_BOX or head.startswith(_MATROSKA))
    upload.seek(0)
    content = upload.read()
    upload.seek(0)
    if head.startswith(_OFFICE_AND_ARCHIVES):
        raise ValidationError(
            f"{name}: Word, Excel and archive files aren’t accepted. "
            "Share a Google Docs or OneDrive link instead."
        )
    content_type = _content_type(head, content)
    if content_type is None:
        raise ValidationError(f"{name}: attach images, video, audio, PDF or plain text.")
    is_video = content_type.startswith("video/")
    if upload.size > (MAX_VIDEO_SIZE if is_video else MAX_SIZE):
        raise _too_big(name, is_video)
    if content_type == "text/plain" and not name.lower().endswith(_TEXT_ENDINGS):
        name = f"{name[:251]}.txt"
    return CheckedFile(upload, name, content_type, upload.size)
