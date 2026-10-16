from typing import Any

from django import forms
from django.core.exceptions import ValidationError

from .uploads import MAX_FILES, CheckedFile, check_upload


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class AttachmentsField(forms.FileField):
    """Up to five files sent at once, each checked by its content. Cleans to a list of
    CheckedFile; every problem is reported, so the client can fix them all in one go."""

    widget = MultipleFileInput

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(required=False, **kwargs)

    def clean(self, data: Any, initial: Any = None) -> list[CheckedFile]:
        uploads = [upload for upload in (data or []) if upload]
        if len(uploads) > MAX_FILES:
            raise ValidationError(f"Attach up to {MAX_FILES} files at a time.")
        checked, errors = [], []
        for upload in uploads:
            try:
                checked.append(check_upload(upload))
            except ValidationError as error:
                errors.append(error)
        if errors:
            raise ValidationError(errors)
        return checked
