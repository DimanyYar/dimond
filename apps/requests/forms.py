from collections.abc import Iterable
from typing import Any
from uuid import UUID

from django import forms
from django.core.exceptions import ValidationError
from django.db import models

from apps.files.forms import AttachmentsField
from apps.files.models import Attachment
from apps.files.uploads import CheckedFile, check_upload

from .models import FORM_TYPES, REQUEST_FIELDS, Request, RequestType, WhenNeeded
from .selectors import Period, Show

# Labels, hints and examples change with the request type (PRD: request types and fields).
# The page also hands this to the browser, so switching the type needs no reload.
TYPE_COPY: dict[str, dict[str, str]] = {
    RequestType.BUG: {
        "title": "e.g. Lead card doesn’t save the second phone number",
        "description_label": "What happened?",
        "description_hint": "What did you do, what did you expect, and what happened instead? "
        "A couple of sentences is plenty.",
        "description": "I added a second phone to a lead and saved it — after a reload the "
        "number was gone.",
        "why": "e.g. Managers can’t call clients back on the second number",
        "link_label": "Link to the lead or page",
        "link": "https://crm.example.com/leads/12345",
    },
    RequestType.IMPROVEMENT: {
        "title": "e.g. Filter leads by source on the dashboard",
        "description_label": "What should change?",
        "description_hint": "How it works now, and how it should work instead.",
        "description": "Now we filter by source by hand every Monday. It would be great to pick "
        "a source right on the dashboard.",
        "why": "e.g. Saves the team a Monday morning every week",
        "link_label": "Link",
        "link": "The page it’s about",
    },
    RequestType.FEATURE: {
        "title": "e.g. Show which leads came from webinars",
        "description_label": "What should it do?",
        "description_hint": "Describe it from the user’s side: who does what, and what they get.",
        "description": "When a lead registers for a webinar, we want to see it on the lead card "
        "and filter by it.",
        "why": "e.g. We start webinars next month and need to measure them",
        "link_label": "Link",
        "link": "An example, a mockup, a spec",
    },
    RequestType.IDEA: {
        "title": "e.g. Remind us about leads that went quiet?",
        "description_label": "What’s on your mind?",
        "description_hint": "Rough thoughts are welcome — we’ll shape it together.",
        "description": "We keep forgetting leads that went silent for a couple of weeks. Maybe "
        "the CRM could nudge us?",
        "why": "e.g. We lose deals we could have saved",
        "link_label": "Link",
        "link": "Anything that inspired it",
    },
}

WHEN_NEEDED_COPY: dict[str, dict[str, str]] = {
    WhenNeeded.HOTFIX: {
        "short": "Blocks our work now",
        "note": "Hotfixes go outside the release plan. If production is down, message your PM "
        "in chat now instead of waiting for this form.",
    },
    WhenNeeded.THIS_RELEASE: {
        "short": "Within the current cycle",
        "note": "We’ll try to fit it into the release that’s in progress. If it doesn’t fit, "
        "Dimon will tell you where it lands.",
    },
    WhenNeeded.NEXT_RELEASE: {
        "short": "Can wait a cycle",
        "note": "Dimon will plan it for the next release and show the date here.",
    },
    WhenNeeded.NICE_TO_HAVE: {
        "short": "Whenever there’s room",
        "note": "It goes to the “Later” list — we pick from it when there’s room. You’ll see "
        "it move.",
    },
    WhenNeeded.NOT_SURE: {
        "short": "Let Dimon suggest",
        "note": "Dimon will suggest a slot based on what’s already planned.",
    },
}


class RequestForm(forms.Form):
    """The request form, empty for a new request or filled in when editing one."""

    type = forms.ChoiceField(choices=FORM_TYPES, initial=RequestType.BUG)
    title = forms.CharField(
        max_length=200, error_messages={"required": "Give it a title in one line."}
    )
    description = forms.CharField(required=False, widget=forms.Textarea)
    steps = forms.CharField(required=False, widget=forms.Textarea)
    why = forms.CharField(
        error_messages={"required": "Tell us why it matters — it helps us plan it right."}
    )
    when_needed = forms.ChoiceField(choices=WhenNeeded.choices, initial=WhenNeeded.NOT_SURE)
    link = forms.URLField(
        max_length=500,
        required=False,
        assume_scheme="https",
        error_messages={"invalid": "Paste the full link, like https://crm.example.com/leads/1."},
    )

    def clean(self) -> dict[str, Any]:
        super().clean()
        data = self.cleaned_data
        if data.get("type") == RequestType.BUG:
            if not data.get("link") and "link" not in self.errors:
                self.add_error("link", "Add a link to the lead or page where it happens.")
        else:
            # Steps can stay in the hidden field after switching away from Bug.
            data["steps"] = ""
        return data

    def type_copy(self) -> dict[str, str]:
        """Copy for the type being shown: the one submitted, or the default on a fresh form."""
        type = self["type"].value()
        return TYPE_COPY.get(type, TYPE_COPY[RequestType.BUG])

    def when_needed_options(self) -> list[dict[str, str]]:
        return [
            {"value": value, "label": label, "short": WHEN_NEEDED_COPY[value]["short"]}
            for value, label in WhenNeeded.choices
        ]

    def when_note(self) -> str:
        return WHEN_NEEDED_COPY.get(
            self["when_needed"].value(), WHEN_NEEDED_COPY[WhenNeeded.NOT_SURE]
        )["note"]


class NewRequestForm(RequestForm):
    """The request form plus files."""

    attachments = AttachmentsField()


class VoiceRequestForm(forms.Form):
    """ "Just say it": the recording, a title and, if the client wants, the type."""

    title = forms.CharField(
        max_length=200, error_messages={"required": "Give it a title in one line."}
    )
    type = forms.ChoiceField(choices=FORM_TYPES, required=False)
    recording = forms.FileField(error_messages={"required": "Record your request first."})

    def clean_recording(self) -> CheckedFile:
        checked = check_upload(self.cleaned_data["recording"])
        if not checked.content_type.startswith("audio/"):
            raise ValidationError("The recording should be sound only. Record it again.")
        return checked

    def request_type(self) -> RequestType:
        return RequestType(self.cleaned_data["type"] or RequestType.NOT_SURE)


class EditRequestForm(RequestForm):
    """The request form for an edit: new files to add and the request's files to remove."""

    attachments = AttachmentsField()
    remove = forms.TypedMultipleChoiceField(required=False, coerce=UUID)

    def __init__(self, *args: Any, files_shown: Iterable[Attachment], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.files_shown = list(files_shown)
        remove = self.fields["remove"]
        assert isinstance(remove, forms.TypedMultipleChoiceField)  # declared just above
        remove.choices = [(str(file.id), file.name) for file in self.files_shown]

    @classmethod
    def for_request(
        cls,
        request: Request,
        files_shown: Iterable[Attachment],
        data: Any = None,
        files: Any = None,
    ) -> "EditRequestForm":
        """The request's current text, or what was just submitted."""
        return cls(
            data,
            files,
            initial={field: getattr(request, field) for field in REQUEST_FIELDS},
            files_shown=files_shown,
        )

    def removing(self) -> set[str]:
        """Ids ticked for removal, as sent: they stay ticked when the form comes back."""
        return set(self["remove"].value() or [])


class View(models.TextChoices):
    BOARD = "board", "Board"
    LIST = "list", "List"


class MyRequestsFilter(forms.Form):
    """The "My requests" filters, kept in the address so a view can be bookmarked or shared.

    A value the page doesn't know (an old bookmark, a typo) falls back to the default
    instead of an error page.
    """

    show = forms.ChoiceField(choices=Show.choices, required=False)
    type = forms.MultipleChoiceField(choices=FORM_TYPES, required=False)
    period = forms.ChoiceField(choices=Period.choices, required=False)
    view = forms.ChoiceField(choices=View.choices, required=False)

    def chosen(self, name: str, default: str = "") -> str:
        self.is_valid()
        value: str = self.cleaned_data.get(name) or default
        return value

    def requests_filter(self) -> tuple[Show, list[RequestType], Period]:
        """Which requests to show: everything here but the board / list switch."""
        show = Show(self.chosen("show", Show.OPEN))
        return show, self.chosen_types(), Period(self.chosen("period", Period.THREE_MONTHS))

    def chosen_types(self) -> list[RequestType]:
        """Several types at once (PRD mockups: the type chips are a multi-select)."""
        self.is_valid()
        return [RequestType(value) for value in self.cleaned_data.get("type") or []]


class MessageForm(forms.Form):
    text = forms.CharField(max_length=5000, widget=forms.Textarea, required=False)
    ask_for_input = forms.BooleanField(required=False)
    attachments = AttachmentsField()

    def clean(self) -> dict[str, Any]:
        super().clean()
        data = self.cleaned_data
        # A screenshot alone is a fine reply; an empty one is not.
        has_files = data.get("attachments") or "attachments" in self.errors
        if not data.get("text") and not has_files:
            self.add_error("text", "Write a message or attach a file.")
        return data


class WithdrawForm(forms.Form):
    reason = forms.CharField(
        max_length=5000,
        required=False,
        widget=forms.Textarea,
        error_messages={"required": "Tell Dimon why — it helps decide what to do with the work."},
    )

    def __init__(self, *args: Any, reason_required: bool, **kwargs: Any) -> None:
        """The reason is optional for an instant withdrawal and required for an ask."""
        super().__init__(*args, **kwargs)
        self.fields["reason"].required = reason_required


class WithdrawalDecisionForm(forms.Form):
    decision = forms.ChoiceField(choices=[("confirm", "Withdraw it"), ("keep", "Keep it going")])
