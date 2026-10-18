import re
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from . import selectors
from .models import Company, Notify


class EmailForm(forms.Form):
    email = forms.EmailField(label="Work email")


class WelcomeForm(forms.Form):
    name = forms.CharField(label="Your name", max_length=120)
    # "Nothing" lives in account settings; a newcomer who picks it would miss the first answer.
    notify = forms.ChoiceField(
        choices=[(Notify.OWN, Notify.OWN.label), (Notify.COMPANY, Notify.COMPANY.label)],
        initial=Notify.OWN,
    )


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
        choices=[(Notify.OWN, "Their own requests"), (Notify.COMPANY, "Everything at the company")],
        initial=Notify.OWN,
    )
    send_anyway = forms.BooleanField(required=False)

    def __init__(self, *args: Any, company: Company, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.company = company
        self.outside: list[str] = []

    def clean_emails(self) -> list[str]:
        emails = list(
            dict.fromkeys(
                e.lower() for e in EMAIL_SEPARATORS.split(self.cleaned_data["emails"]) if e
            )
        )
        if not emails:
            raise ValidationError("Add an email.")
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

    def clean(self) -> dict[str, Any]:
        super().clean()
        cleaned = self.cleaned_data
        domain = self.company.email_domain.lower()
        if domain and "emails" in cleaned:
            self.outside = [e for e in cleaned["emails"] if not e.endswith("@" + domain)]
            if self.outside and not cleaned.get("send_anyway"):
                raise ValidationError(
                    "Check the addresses outside the company domain.", code="outside"
                )
        return cleaned

    def asks_to_confirm(self) -> bool:
        return bool(self.outside) and any(
            e.code == "outside" for e in self.non_field_errors().as_data()
        )


def _is_email(value: str) -> bool:
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True
