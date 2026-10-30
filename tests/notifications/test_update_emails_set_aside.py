"""When dimond cannot build one request's emails, that request is set aside and the others
still go out (PRD: email notifications). Nobody gets the same email twice, and Logs → Emails
shows who missed theirs."""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.models import Company, Notify, User
from apps.notifications import services as notifications
from apps.notifications.models import Email, EmailKind, EmailStatus
from apps.notifications.services import (
    AFTER_ERROR,
    GROUPING,
    NOT_BUILT,
    NOT_SURE,
    send_request_updates,
)
from apps.requests import services
from apps.requests.models import Request, RequestType, WhenNeeded

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", email_domain="atlas-crm.com", owner=pm)


def client(company: Company, email: str, name: str) -> User:
    return User.objects.create_user(
        email, name=name, company=company, notify=Notify.COMPANY, last_login=timezone.now()
    )


@pytest.fixture
def mark(atlas) -> User:
    return client(atlas, "mark.t@atlas-crm.com", "Mark T.")


@pytest.fixture
def anna(atlas) -> User:
    return client(atlas, "anna.k@atlas-crm.com", "Anna K.")


def new_request(author: User, title: str) -> Request:
    return services.create_request(
        author, RequestType.BUG, title, why="Sales export weekly", when_needed=WhenNeeded.HOTFIX
    )


def settle() -> int:
    return send_request_updates(now=timezone.now() + GROUPING + timedelta(seconds=1))


def reply_after_the_request_email(author: User, request: Request) -> None:
    """Mark replies once the email about the new request went out: Anna and the PM both
    have news about it."""
    settle()
    mail.outbox.clear()
    Email.objects.all().delete()
    services.post_message(author, request, "It also happens on 5k rows")


@pytest.fixture
def broken_pm_email(monkeypatch):
    """The PM's email template breaks, the way a bug in it would."""
    render = notifications.render_to_string

    def render_or_break(template: str, context: dict[str, object]) -> str:
        if "pm_update" in template:
            raise KeyError("meta")
        return render(template, context)

    monkeypatch.setattr(notifications, "render_to_string", render_or_break)


def test_nobody_is_emailed_when_one_email_of_the_batch_cannot_be_built(mark, anna, broken_pm_email):
    request = new_request(mark, "Leads export times out")
    reply_after_the_request_email(mark, request)

    for _ in range(3):
        settle()

    assert mail.outbox == []


def test_logs_show_everyone_who_missed_an_email_that_could_not_be_built(
    mark, anna, pm, broken_pm_email
):
    request = new_request(mark, "Leads export times out")
    reply_after_the_request_email(mark, request)

    settle()

    assert set(Email.objects.values_list("user", "kind", "status", "error")) == {
        (anna.pk, EmailKind.REQUEST_UPDATE, EmailStatus.FAILED, NOT_BUILT),
        (pm.pk, EmailKind.PM_UPDATE, EmailStatus.FAILED, NOT_BUILT),
    }
    missed = Email.objects.get(user=pm)
    assert missed.subject == f"{request.key} · Leads export times out"
    assert missed.error_detail == "KeyError: 'meta'"


def test_an_email_that_broke_while_going_out_is_not_called_unsent_or_sent_again(
    mark, anna, pm, monkeypatch
):
    request = new_request(mark, "Leads export times out")
    reply_after_the_request_email(mark, request)
    send = notifications.send_email

    def send_then_break(user: User, kind: EmailKind, *args: str) -> bool:
        send(user, kind, *args)
        if kind == EmailKind.PM_UPDATE:
            raise RuntimeError("journal")
        return True

    monkeypatch.setattr(notifications, "send_email", send_then_break)

    settle()
    settle()

    assert len([m for m in mail.outbox if m.to == ["dmitry@shakuro.com"]]) == 1
    assert Email.objects.get(user=anna).status == EmailStatus.SENT
    unsure = Email.objects.get(user=pm)
    assert unsure.error == NOT_SURE
    assert unsure.error_detail == "RuntimeError: journal"


def test_one_broken_request_does_not_hold_up_the_others(mark, anna, pm, monkeypatch):
    broken = new_request(mark, "Leads export times out")
    fine = new_request(mark, "Invoices show the wrong currency")
    render = notifications.render_to_string

    def break_one_request(template: str, context: dict[str, object]) -> str:
        if context["request"] == broken:
            raise KeyError("meta")
        return render(template, context)

    monkeypatch.setattr(notifications, "render_to_string", break_one_request)

    assert settle() == 1
    [email] = mail.outbox
    assert fine.key in email.subject
    assert Email.objects.get(error=NOT_BUILT).subject.startswith(broken.key)


def test_pm_sees_news_that_broke_before_its_readers_were_known(mark, pm, monkeypatch):
    request = new_request(mark, "Leads export times out")

    def broken_selector(request: Request) -> User | None:
        raise ValueError("no company")

    monkeypatch.setattr(notifications, "pm_recipient", broken_selector)

    assert settle() == 0
    missed = Email.objects.get()
    assert missed.user == pm
    assert missed.subject == f"{request.key} · Leads export times out"
    assert missed.error_detail == "ValueError: no company"


def test_set_aside_news_is_not_tried_again(mark, pm, broken_pm_email):
    new_request(mark, "Leads export times out")

    settle()
    settle()

    assert Email.objects.filter(error=NOT_BUILT).count() == 1


def test_people_after_an_email_that_broke_while_going_out_are_logged_as_held_back(
    mark, anna, monkeypatch
):
    request = new_request(mark, "Leads export times out")
    reply_after_the_request_email(mark, request)
    monkeypatch.setattr(notifications, "send_email", lambda *args: 1 / 0)

    settle()

    assert set(Email.objects.values_list("kind", "error")) == {
        (EmailKind.REQUEST_UPDATE, NOT_SURE),
        (EmailKind.PM_UPDATE, AFTER_ERROR),
    }
