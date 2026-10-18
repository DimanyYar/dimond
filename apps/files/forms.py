from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils.datastructures import MultiValueDict

from .uploads import MAX_FILES, CheckedFile, check_poster, check_upload


class MultipleFileInput(forms.ClearableFileInput):
    """The files, and next to them the frames the browser took from the videos among them: sent
    as "<field>_posters", each named after its video plus ".jpg"."""

    allow_multiple_selected = True

    def value_from_datadict(
        self, data: Mapping[str, Any], files: MultiValueDict[str, Any], name: str
    ) -> tuple[list[UploadedFile[Any]], dict[str, UploadedFile[Any]]]:
        uploads = super().value_from_datadict(data, files, name)
        posters = {poster.name: poster for poster in files.getlist(f"{name}_posters")}
        return uploads, posters


class AttachmentsField(forms.FileField):
    """Up to five files sent at once, each checked by its content. Cleans to a list of
    CheckedFile; every problem is reported, so the client can fix them all in one go."""

    widget = MultipleFileInput

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(required=False, **kwargs)

    def clean(self, data: Any, initial: Any = None) -> list[CheckedFile]:
        sent, posters = data
        uploads = [upload for upload in (sent or []) if upload]
        if len(uploads) > MAX_FILES:
            raise ValidationError(f"Attach up to {MAX_FILES} files at a time.")
        checked, errors = [], []
        for upload in uploads:
            try:
                file = check_upload(upload)
            except ValidationError as error:
                errors.append(error)
                continue
            poster = posters.get(f"{upload.name}.jpg")
            if file.content_type.startswith("video/") and poster is not None:
                file = file._replace(poster=check_poster(poster))
            checked.append(file)
        if errors:
            raise ValidationError(errors)
        return checked
