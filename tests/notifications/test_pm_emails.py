"""The PM's emails about their own clients (PRD: email notifications): a new request, a
client's reply and an ask to withdraw, the same things Needs you shows."""

from datetime import timedelta

import pytest
from django.conf import settings
from django.core import mail
from django.utils import timezone

from apps.accounts.models import Company, Notify, Role, User
from apps.notifications.models import Email, EmailKind
from apps.notifications.services import GROUPING, send_request_updates
from apps.requests import services
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", email_domain="atlas-crm.com", owner=pm)


@pytest.fixture
def mark(atlas) -> User:
    # "Nothing": only the PM's emails count in these tests.
    return User.objects.create_user(
        "mark.t@atlas-crm.com", name="Mark T.", company=atlas, notify=Notify.NONE
    )


def new_request(author: User, title: str = "Leads export times out on 10k+ rows") -> Request:
    return services.create_request(
        author, RequestType.BUG, title, why="Sales export weekly", when_needed=WhenNeeded.HOTFIX
    )


def settle() -> int:
    return send_request_updates(now=timezone.now() + GROUPING + timedelta(seconds=1))


def to_pm() -> list:
    return [message for message in mail.outbox if message.to == ["dmitry@shakuro.com"]]


def html(message) -> str:
    return message.alternatives[0][0]


def test_pm_hears_about_a_new_request_from_their_client(mark):
    request = new_request(mark)

    assert settle() == 1
    [email] = to_pm()
    assert email.subject == (
        f"Atlas · {request.key} · Leads export times out on 10k+ rows — new request"
    )
    assert "New request from Mark T." in html(email)
    assert "Bug · asked for: Hotfix" in html(email)
    assert f"{settings.SITE_URL}/account/" in html(email)


def test_new_request_and_a_quick_follow_up_make_one_email(mark):
    request = new_request(mark)
    services.post_message(mark, request, "It also happens on 5k rows")

    settle()

    [email] = to_pm()
    assert email.subject.endswith("— Mark T. replied")
    assert "New request from Mark T." in html(email)


def test_pm_hears_about_a_client_reply_without_its_text(mark, pm):
    request = new_request(mark)
    settle()
    mail.outbox.clear()

    services.post_message(mark, request, "Only on Chrome")
    settle()

    [email] = to_pm()
    assert "Mark T. replied" in html(email)
    assert "Chrome" not in html(email)
    assert "Chrome" not in email.body


def test_pm_hears_about_an_ask_to_withdraw(mark, pm):
    request = new_request(mark)
    services.change_status(request, ClientStatus.IN_PROGRESS, pm)
    settle()
    mail.outbox.clear()

    services.withdraw_request(mark, request, "We found a workaround")
    settle()

    [email] = to_pm()
    assert email.subject.endswith("— asks to withdraw")
    assert "Withdraw it or Keep it going" in html(email)
    assert "workaround" not in html(email)


def test_instant_withdrawal_sends_the_pm_nothing(mark):
    request = new_request(mark)
    settle()
    mail.outbox.clear()

    services.withdraw_request(mark, request, "Not needed any more")
    settle()

    assert to_pm() == []


def test_request_created_and_withdrawn_within_minutes_sends_the_pm_nothing(mark):
    request = new_request(mark)
    services.withdraw_request(mark, request, "Sent it by mistake")

    settle()

    assert to_pm() == []


def test_pm_who_turned_emails_off_gets_none(mark, pm):
    pm.notify = Notify.NONE
    pm.save()
    new_request(mark)

    assert settle() == 0


def test_pm_never_hears_about_another_pms_client(pm):
    olga = User.objects.create_user("olga@shakuro.com", name="Olga", role=Role.PM)
    northwind = Company.objects.create(name="Northwind", owner=olga)
    paul = User.objects.create_user("paul@northwind.co", company=northwind, notify=Notify.NONE)
    new_request(paul)

    settle()

    assert [message.to for message in mail.outbox] == [["olga@shakuro.com"]]


def test_pm_gets_no_email_about_their_own_reply(mark, pm):
    request = new_request(mark)
    settle()
    mail.outbox.clear()

    services.post_message(pm, request, "On it")
    settle()

    assert to_pm() == []


def test_pm_email_is_journaled_as_to_the_pm(mark):
    new_request(mark)

    settle()

    assert Email.objects.get().kind == EmailKind.PM_UPDATE
