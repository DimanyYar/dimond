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
from apps.notifications.services import GROUPING, NOT_BUILT, send_request_updates
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


def test_client_is_not_emailed_again_when_the_pm_email_cannot_be_built(mark, anna, broken_pm_email):
    request = new_request(mark, "Leads export times out")
    reply_after_the_request_email(mark, request)

    for _ in range(3):
        settle()

    assert [message.to for message in mail.outbox] == [["anna.k@atlas-crm.com"]]


def test_logs_show_who_missed_an_email_that_could_not_be_built(mark, anna, pm, broken_pm_email):
    request = new_request(mark, "Leads export times out")
    reply_after_the_request_email(mark, request)

    settle()

    sent = Email.objects.get(user=anna)
    missed = Email.objects.get(user=pm)
    assert sent.status == EmailStatus.SENT
    assert missed.kind == EmailKind.PM_UPDATE
    assert missed.status == EmailStatus.FAILED
    assert missed.subject == f"{request.key} · Leads export times out"
    assert missed.error == NOT_BUILT
    assert missed.error_detail == "KeyError: 'meta'"


def test_everyone_after_the_broken_email_is_logged_as_missing_it(mark, anna, pm, monkeypatch):
    request = new_request(mark, "Leads export times out")
    reply_after_the_request_email(mark, request)
    monkeypatch.setattr(notifications, "render_to_string", lambda template, context: 1 / 0)

    settle()

    assert mail.outbox == []
    assert set(Email.objects.values_list("user", "error")) == {
        (anna.pk, NOT_BUILT),
        (pm.pk, NOT_BUILT),
    }


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
