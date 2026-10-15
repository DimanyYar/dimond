from typing import Any

from django import forms
from django.db import models

from .models import RequestType, WhenNeeded
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


class NewRequestForm(forms.Form):
    type = forms.ChoiceField(choices=RequestType.choices, initial=RequestType.BUG)
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


class View(models.TextChoices):
    BOARD = "board", "Board"
    LIST = "list", "List"


class MyRequestsFilter(forms.Form):
    """The "My requests" filters, kept in the address so a view can be bookmarked or shared.

    A value the page doesn't know (an old bookmark, a typo) falls back to the default
    instead of an error page.
    """

    show = forms.ChoiceField(choices=Show.choices, required=False)
    type = forms.MultipleChoiceField(choices=RequestType.choices, required=False)
    period = forms.ChoiceField(choices=Period.choices, required=False)
    view = forms.ChoiceField(choices=View.choices, required=False)

    def chosen(self, name: str, default: str = "") -> str:
        self.is_valid()
        value: str = self.cleaned_data.get(name) or default
        return value

    def chosen_types(self) -> list[RequestType]:
        """Several types at once (PRD mockups: the type chips are a multi-select)."""
        self.is_valid()
        return [RequestType(value) for value in self.cleaned_data.get("type") or []]
