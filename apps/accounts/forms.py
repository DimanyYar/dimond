import re
from typing import Any, cast

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from apps.tracker.base import TrackerError
from apps.tracker.connection import current_tracker

from . import selectors
from .models import Company, Notify, User


class EmailForm(forms.Form):
    email = forms.EmailField(label="Work email")


class WelcomeForm(forms.Form):
    name = forms.CharField(label="Your name", max_length=120)
    # "Nothing" lives in account settings; a newcomer who picks it would miss the first answer.
    notify = forms.ChoiceField(
        choices=[(Notify.OWN, Notify.OWN.label), (Notify.COMPANY, Notify.COMPANY.label)],
        initial=Notify.OWN,
    )


class PersonForm(forms.Form):
    """The Profile block on the person page: what the PM may change for someone."""

    name = forms.CharField(max_length=120, required=False, strip=True)
    notify = forms.ChoiceField(choices=Notify.choices)


class AccountNameForm(forms.Form):
    name = forms.CharField(
        max_length=120, error_messages={"required": "Your name shows on your requests."}
    )


class AccountEmailsForm(forms.Form):
    notify = forms.ChoiceField(choices=Notify.choices)


# A team's worth at once; a bigger list is more likely a paste mistake than a plan.
MAX_INVITES_AT_ONCE = 20
EMAIL_SEPARATORS = re.compile(r"[\s,;]+")


class InviteForm(forms.Form):
    """The PM's Invite people form on the Clients page (PRD: Clients screen).

    Addresses come pasted one per line or separated by commas. An address that already has an
    account is an error: one email is one person (ADR 0011). An address outside the company's
    domain is allowed after the PM confirms it, since contractors exist.
    """

    emails = forms.CharField(
        label="Work emails", widget=forms.Textarea, error_messages={"required": "Add an email."}
    )
    notify = forms.ChoiceField(
        choices=[
            (Notify.OWN, "Their requests"),
            (Notify.COMPANY, "Their requests and their team’s"),
        ],
        initial=Notify.OWN,
    )
    send_anyway = forms.BooleanField(required=False)

    def __init__(self, *args: Any, company: Company, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.company = company
        self.outside: list[str] = []

    def clean_emails(self) -> list[str]:
        emails = parse_emails(self.cleaned_data["emails"])
        if not emails:
            raise ValidationError("Add an email.")
        return emails

    def clean(self) -> dict[str, Any]:
        super().clean()
        self.outside = _outside_domain(self.cleaned_data, self.company.email_domain)
        _stop_for_outside(self)
        return self.cleaned_data

    def asks_to_confirm(self) -> bool:
        return _asks_to_confirm(self)


def _is_email(value: str) -> bool:
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def parse_emails(text: str) -> list[str]:
    """The addresses pasted into an invite field, lower-cased, without repeats. Raises
    ValidationError for anything that cannot be invited (PRD: Clients screen)."""
    emails = list(dict.fromkeys(e.lower() for e in EMAIL_SEPARATORS.split(text) if e))
    if len(emails) > MAX_INVITES_AT_ONCE:
        raise ValidationError(f"Up to {MAX_INVITES_AT_ONCE} people at once.")
    wrong = [email for email in emails if not _is_email(email)]
    if wrong:
        raise ValidationError(f"Not an email: {', '.join(wrong)}")
    taken = [email for email in emails if selectors.email_has_account(email)]
    if taken:
        # Never which company: it may be another PM's client.
        raise ValidationError(f"Already has a dimon.d account: {', '.join(taken)}")
    return emails


def _outside_domain(cleaned: dict[str, Any], domain: str) -> list[str]:
    """Addresses outside the company's domain. They stop the form once, until the PM presses
    Send anyway: contractors exist, typos too (the form raises when this is not empty)."""
    if not domain or not cleaned.get("emails"):
        return []
    return [e for e in cleaned["emails"] if not e.endswith("@" + domain.lower())]


def _stop_for_outside(form: "InviteForm | NewClientForm") -> None:
    if form.outside and not form.cleaned_data.get("send_anyway"):
        raise ValidationError("Check the addresses outside the company domain.", code="outside")


def _asks_to_confirm(form: forms.Form) -> bool:
    return any(e.code == "outside" for e in form.non_field_errors().as_data())


# The sidebar colours of the UI kit; a client is told apart by one of them.
CLIENT_COLORS = [
    ("#ff5600", "Orange"),
    ("#03b2cb", "Teal"),
    ("#b3e01c", "Lime"),
    ("#6e56cf", "Violet"),
    ("#f2a6c7", "Pink"),
    ("#9c9fa5", "Gray"),
]
# Each label: letters, digits and inner hyphens, up to 63 characters; at least two labels.
DOMAIN_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
DOMAIN = re.compile(rf"^{DOMAIN_LABEL}(?:\.{DOMAIN_LABEL})+$")


JIRA_PROJECT_ERRORS = {
    "required": "Choose the client's Jira project.",
    "invalid_choice": "This project is gone from Jira, or the tracker account cannot see it "
    "any more.",
}


def fill_jira_projects(field: forms.ChoiceField, keep: str = "") -> str:
    """Puts the projects the tracker account sees into `field` and returns the tracker's
    problem, if any, in plain words. The list is asked for again when a form is sent, so the
    chosen key is checked against the tracker itself (PRD: tracker and settings).

    `keep` is the client's current project: it stays on the list even if the tracker no
    longer shows it, so saving a form without changing it never fails.
    """
    try:
        projects = current_tracker().projects()
        error = ""
    except TrackerError as problem:
        projects = []
        error = f"Couldn't load the projects: {problem}"
        field.error_messages |= {"required": error, "invalid_choice": error}
    choices = [(project.key, f"{project.key} · {project.name}") for project in projects]
    if keep and keep not in {key for key, _ in choices}:
        choices.insert(0, (keep, f"{keep} · not visible to the tracker account now"))
    field.choices = choices
    return error


class JiraProjectForm(forms.Form):
    """The PM changes a client's Jira project on the Clients page. New tasks go there; tasks
    created before keep their keys."""

    jira_project = forms.ChoiceField(error_messages=JIRA_PROJECT_ERRORS)

    def __init__(self, *args: Any, company: Company, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.company = company
        self.tracker_error = fill_jira_projects(
            cast(forms.ChoiceField, self.fields["jira_project"]), keep=company.jira_project
        )
        self.fields["jira_project"].initial = company.jira_project


class NewClientForm(forms.Form):
    """The PM's New client window: a company, its Jira project and, optionally, its first
    invites (PRD: Clients screen; tracker and settings)."""

    name = forms.CharField(
        max_length=120, error_messages={"required": "Name the client."}, strip=True
    )
    email_domain = forms.CharField(max_length=120, required=False)
    color = forms.ChoiceField(choices=CLIENT_COLORS)
    jira_project = forms.ChoiceField(error_messages=JIRA_PROJECT_ERRORS)
    emails = forms.CharField(widget=forms.Textarea, required=False)
    notify = forms.ChoiceField(
        choices=[
            (Notify.OWN, "Their requests"),
            (Notify.COMPANY, "Their requests and their team’s"),
        ],
        initial=Notify.OWN,
    )
    send_anyway = forms.BooleanField(required=False)

    def __init__(self, *args: Any, owner: User, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.owner = owner
        self.outside: list[str] = []
        self.fields["color"].initial = selectors.next_client_color(owner, CLIENT_COLORS)
        self.tracker_error = fill_jira_projects(
            cast(forms.ChoiceField, self.fields["jira_project"])
        )

    def clean_name(self) -> str:
        name: str = self.cleaned_data["name"]
        if selectors.client_for_name(self.owner, name) is not None:
            raise ValidationError("You already have a client with this name.")
        return name

    def clean_email_domain(self) -> str:
        domain: str = self.cleaned_data["email_domain"].strip().lower().removeprefix("@")
        if domain and not DOMAIN.match(domain):
            raise ValidationError("A domain looks like company.com, without @ or https://")
        return domain

    def clean_emails(self) -> list[str]:
        return parse_emails(self.cleaned_data["emails"])

    def clean(self) -> dict[str, Any]:
        super().clean()
        self.outside = _outside_domain(self.cleaned_data, self.cleaned_data.get("email_domain", ""))
        _stop_for_outside(self)
        return self.cleaned_data

    def asks_to_confirm(self) -> bool:
        return _asks_to_confirm(self)
