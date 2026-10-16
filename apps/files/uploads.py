"""What a file is, judged by its content, and whether dimond accepts it (PRD: access and
security, attachments).

A file's name says nothing reliable: an HTML page renamed to "photo.png" is still a page that
can run scripts. So the type comes from the first bytes, which every format starts with.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile

MAX_FILES = 5
MAX_SIZE = 10 * 1024 * 1024

# The first bytes each accepted format starts with.
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
)
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


def _content_type(head: bytes, content: bytes) -> str | None:
    for signature, content_type in _SIGNATURES:
        if head.startswith(signature):
            return content_type
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
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


def check_upload(upload: UploadedFile[Any]) -> CheckedFile:
    """Accept images, PDF and plain text up to 10 MB; raise ValidationError with a message for
    the client otherwise."""
    name = (upload.name or "file")[-255:]
    if not upload.size:
        raise ValidationError(f"{name} is empty.")
    if upload.size > MAX_SIZE:
        raise ValidationError(f"{name} is over 10 MB. Make it smaller or share a link instead.")
    upload.seek(0)
    content = upload.read()
    upload.seek(0)
    head = content[:16]
    if head.startswith(_OFFICE_AND_ARCHIVES):
        raise ValidationError(
            f"{name}: Word, Excel and archive files aren’t accepted. "
            "Share a Google Docs or OneDrive link instead."
        )
    content_type = _content_type(head, content)
    if content_type is None:
        raise ValidationError(f"{name}: attach images, PDF or plain text.")
    if content_type == "text/plain" and not name.lower().endswith(_TEXT_ENDINGS):
        name = f"{name[:251]}.txt"
    return CheckedFile(upload, name, content_type, upload.size)
