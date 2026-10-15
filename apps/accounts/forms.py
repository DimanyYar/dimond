from django import forms

from .models import Notify


class EmailForm(forms.Form):
    email = forms.EmailField(label="Work email")


class WelcomeForm(forms.Form):
    name = forms.CharField(label="Your name", max_length=120)
    # "Nothing" lives in account settings; a newcomer who picks it would miss the first answer.
    notify = forms.ChoiceField(
        choices=[(Notify.OWN, Notify.OWN.label), (Notify.COMPANY, Notify.COMPANY.label)],
        initial=Notify.OWN,
    )
