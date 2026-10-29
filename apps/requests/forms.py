from collections.abc import Iterable
from typing import Any, cast
from uuid import UUID

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.files.forms import AttachmentsField
from apps.files.models import Attachment
from apps.files.uploads import CheckedFile, check_upload
from apps.tracker.base import ISSUE_KEY, TrackerError
from apps.tracker.connection import current_tracker
from apps.tracker.forms import fill_sprints, sprint_field

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
    """ "Just say it": the recording, a title, the text draft the browser wrote (checked by the
    client) and, if the client wants, the type."""

    title = forms.CharField(
        max_length=200, error_messages={"required": "Give it a title in one line."}
    )
    text = forms.CharField(required=False, max_length=5000)
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
    # The PM's client chip; ignored for clients, who only ever see their own company.
    company = forms.IntegerField(required=False, min_value=1)
    q = forms.CharField(required=False, max_length=200, strip=True)

    def chosen(self, name: str, default: str = "") -> str:
        self.is_valid()
        value: str = self.cleaned_data.get(name) or default
        return value

    def requests_filter(self) -> tuple[Show, list[RequestType], Period]:
        """Which requests to show: everything here but the board / list switch."""
        show = Show(self.chosen("show", Show.OPEN))
        return show, self.chosen_types(), Period(self.chosen("period", Period.THREE_MONTHS))

    def chosen_company(self) -> int | None:
        self.is_valid()
        company: int | None = self.cleaned_data.get("company")
        return company

    def chosen_types(self) -> list[RequestType]:
        """Several types at once (PRD mockups: the type chips are a multi-select)."""
        self.is_valid()
        return [RequestType(value) for value in self.cleaned_data.get("type") or []]


class AnswerForm(forms.Form):
    """The client's answer under the team's question, with files if any (decided Oct 21,
    2026): they go to the Jira task with it."""

    answer = forms.CharField(max_length=5000, widget=forms.Textarea, required=False)
    attachments = AttachmentsField()

    def clean(self) -> dict[str, Any]:
        super().clean()
        data = self.cleaned_data
        has_files = data.get("attachments") or "attachments" in self.errors
        # A screenshot alone answers "which screen?" well enough.
        if not data.get("answer") and not has_files:
            self.add_error("answer", "Write your answer or attach a file.")
        return data


class MessageForm(forms.Form):
    # The PM's switch under the thread: a reply the client reads, or a note only the PM reads.
    kind = forms.ChoiceField(
        choices=[("reply", "Reply"), ("note", "Internal note")], required=False
    )
    text = forms.CharField(max_length=5000, widget=forms.Textarea, required=False)
    ask_for_input = forms.BooleanField(required=False)
    attachments = AttachmentsField()

    def is_note(self) -> bool:
        return self.cleaned_data.get("kind") == "note"

    def clean(self) -> dict[str, Any]:
        super().clean()
        data = self.cleaned_data
        has_files = data.get("attachments") or "attachments" in self.errors
        if self.is_note():
            if has_files:
                self.add_error("attachments", "Notes take text only. Send files in a reply.")
            if not data.get("text"):
                self.add_error("text", "Write the note.")
            return data
        # A screenshot alone is a fine reply; an empty one is not.
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


class TriageForm(forms.Form):
    """The PM's one-click actions on the card."""

    action = forms.ChoiceField(
        choices=[("later", "Move to Later"), ("back", "Bring back"), ("handled", "No reply needed")]
    )


class DeclineForm(forms.Form):
    reason = forms.CharField(
        max_length=5000,
        widget=forms.Textarea,
        error_messages={"required": "Tell the client why: they read it in the thread."},
    )


def jira_description(request: Request) -> str:
    """The task description, in Jira's own markup, from the client's words. The PM edits it
    freely before creating the task and rewrites it into a spec in Jira later; dimond never
    syncs it back (ADR 0010)."""
    sections = [request.description]
    if request.why:
        sections.append(f"h3. Why it matters\n{request.why}")
    if request.steps:
        sections.append(f"h3. Steps to reproduce\n{request.steps}")
    if request.link:
        sections.append(f"h3. Link\n{request.link}")
    link = f"{settings.SITE_URL}{request.get_absolute_url()}"
    voice = " · sent by voice, the recording is in dimond" if request.from_voice else ""
    sections.append(
        f"----\nRequested by {request.author} ({request.company}) · wants: "
        f"{request.get_when_needed_display()}{voice} · dimond: [{request.key}|{link}]"
    )
    return "\n\n".join(section for section in sections if section)


class _ReplyToClient(forms.Form):
    """The optional reply that goes into the client's thread when a task is created or
    linked. Nothing of it reaches Jira."""

    reply = forms.BooleanField(required=False, initial=True)
    reply_text = forms.CharField(
        max_length=5000,
        widget=forms.Textarea,
        required=False,
        initial="Thanks — we’ve passed it to the team. You can follow its status right here.",
    )

    def clean(self) -> dict[str, Any]:
        super().clean()
        if self.cleaned_data.get("reply") and not self.cleaned_data.get("reply_text", "").strip():
            self.add_error("reply_text", "Write the reply, or untick “Reply to the client”.")
        return self.cleaned_data

    def message_for_client(self) -> str:
        """The message for the client's thread, or nothing when the PM chose not to write."""
        return self.cleaned_data["reply_text"].strip() if self.cleaned_data["reply"] else ""


# What goes to Jira (PRD: attachments to Jira): pictures, PDFs and text as files; video and
# voice stay in dimond and the task gets a public link that plays them.
JIRA_FILE_CHOICES = {
    "file": [("file", "Attach file"), ("skip", "Skip")],
    "link": [("link", "Public link"), ("skip", "Skip")],
}


def _jira_way(file: Attachment) -> str:
    return "link" if file.kind in ("video", "audio") else "file"


class _FilesToJira(_ReplyToClient):
    """The reply plus one choice per file of the request: how it goes to Jira, if at all."""

    def __init__(self, *args: Any, files: Iterable[Attachment] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.request_files = list(files)
        for file in self.request_files:
            self.fields[f"file_{file.id.hex}"] = forms.ChoiceField(
                choices=JIRA_FILE_CHOICES[_jira_way(file)], initial=_jira_way(file)
            )

    def file_rows(self) -> list[tuple[Attachment, forms.BoundField]]:
        """Each file with its choice, for the window's list."""
        return [(file, self[f"file_{file.id.hex}"]) for file in self.request_files]

    def files_to(self, way: str) -> list[Attachment]:
        """The files the PM sends to Jira this way: "file" uploads, "link" shares."""
        return [
            file for file in self.request_files if self.cleaned_data[f"file_{file.id.hex}"] == way
        ]


class JiraTaskForm(_FilesToJira):
    """Create Jira task, New task (PRD: "Create task"; mockup: Create Jira task). The project
    is the client's own and is not chosen here."""

    issue_type = forms.ChoiceField()
    summary = forms.CharField(max_length=255, error_messages={"required": "Name the task."})
    description = forms.CharField(widget=forms.Textarea, required=False)
    sprint = sprint_field()

    def __init__(self, *args: Any, request: Request, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        company = request.company
        self.sprints = fill_sprints(
            cast(forms.ChoiceField, self.fields["sprint"]),
            company.jira_project,
            company.jira_sprint_id,
            company.jira_sprint_name,
        )
        self.tracker_error = ""
        try:
            types = current_tracker().issue_types(request.company.jira_project)
        except TrackerError as error:
            types = []
            self.tracker_error = f"Couldn't read the issue types: {error}"
        issue_type = cast(forms.ChoiceField, self.fields["issue_type"])
        issue_type.choices = [(name, name) for name in types]
        # Jira has no Improvement type, so everything but a bug starts as a Story.
        wanted = "Bug" if request.type == RequestType.BUG else "Story"
        issue_type.initial = wanted if wanted in types else next(iter(types), "")
        self.fields["summary"].initial = request.title
        self.fields["description"].initial = jira_description(request)

    def clean(self) -> dict[str, Any]:
        super().clean()
        if self.tracker_error:
            raise ValidationError(self.tracker_error)
        return self.cleaned_data


class LinkJiraTaskForm(_FilesToJira):
    """Create Jira task, Existing task: a task picked from the list or a key typed in, and,
    if ticked, a comment on it with the request's files (decided Oct 19)."""

    key = forms.CharField(
        max_length=32, error_messages={"required": "Pick a task, or type its key."}
    )
    comment = forms.BooleanField(required=False, initial=True)

    def clean_key(self) -> str:
        key: str = self.cleaned_data["key"].strip().upper()
        if not ISSUE_KEY.fullmatch(key):
            raise ValidationError("A Jira key looks like FI-2214.")
        return key
