"""When the mail server is down, emails pause instead of being tried every few seconds, and
one email is tried every 5 minutes until one goes out (PRD: email notifications)."""

import smtplib
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts import services as accounts
from apps.accounts.models import Company, Notify, User
from apps.notifications.models import Email, EmailKind, EmailStatus
from apps.notifications.selectors import PROBE_EVERY, mail_pause, sent_today
from apps.notifications.services import send_queued_emails
from apps.requests import services as requests
from apps.requests.models import ClientStatus, RequestType, StatusEvent, WhenNeeded

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm)


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna.k@atlas-crm.com", name="Anna K.", company=atlas)


@pytest.fixture
def mark(atlas) -> User:
    return User.objects.create_user("mark.t@atlas-crm.com", name="Mark T.", company=atlas)


@pytest.fixture
def server_down(monkeypatch):
    def unreachable(*args, **kwargs):
        raise ConnectionRefusedError

    monkeypatch.setattr("apps.notifications.services.EmailMessage.send", unreachable)
    return monkeypatch


def failing_for(minutes: int) -> None:
    """Make every failed try so far look `minutes` old."""
    then = timezone.now() - timedelta(minutes=minutes)
    Email.objects.update(created_at=then, tried_at=then)


def test_a_minute_of_an_unreachable_server_pauses_emails(anna, server_down):
    accounts.request_sign_in_link(anna.email)
    send_queued_emails()
    assert mail_pause() is None

    failing_for(minutes=2)

    pause = mail_pause()
    assert pause is not None
    assert pause.reason == "Mail server unreachable"


def test_paused_worker_tries_nothing_until_the_next_try(anna, server_down):
    accounts.request_sign_in_link(anna.email)
    send_queued_emails()
    failing_for(minutes=2)

    send_queued_emails()

    assert Email.objects.get().attempts == 1


def test_paused_worker_tries_one_email_every_five_minutes(anna, mark, server_down):
    accounts.request_sign_in_link(anna.email)
    accounts.request_sign_in_link(mark.email)
    send_queued_emails()
    failing_for(minutes=6)

    send_queued_emails()

    assert sorted(Email.objects.values_list("attempts", flat=True)) == [1, 2]


def test_paused_worker_tries_one_email_even_when_a_request_has_several(pm, anna, mark, server_down):
    pm.notify = Notify.NONE
    pm.save(update_fields=["notify"])
    User.objects.filter(pk__in=[anna.pk, mark.pk]).update(
        notify=Notify.COMPANY, last_login=timezone.now()
    )
    feature = requests.create_request(
        anna, RequestType.FEATURE, "Dark mode", why="Night shifts", when_needed=WhenNeeded.NOT_SURE
    )
    requests.change_status(feature, ClientStatus.IN_PROGRESS, None)
    StatusEvent.objects.update(created_at=timezone.now() - timedelta(minutes=10))
    send_queued_emails()
    failing_for(minutes=6)

    send_queued_emails()

    assert sorted(Email.objects.values_list("attempts", flat=True)) == [1, 2]


def test_one_email_going_out_ends_the_pause(anna, mark, server_down):
    accounts.request_sign_in_link(anna.email)
    accounts.request_sign_in_link(mark.email)
    send_queued_emails()
    failing_for(minutes=6)
    server_down.undo()

    assert send_queued_emails() == 1
    assert mail_pause() is None
    assert send_queued_emails() == 1
    assert len(mail.outbox) == 2


def test_a_refused_address_does_not_pause_the_rest(anna, monkeypatch):
    def refuse_address(*args, **kwargs):
        raise smtplib.SMTPRecipientsRefused({"anna.k@atlas-crm.com": (550, b"no such user")})

    monkeypatch.setattr("apps.notifications.services.EmailMessage.send", refuse_address)
    accounts.request_sign_in_link(anna.email)
    send_queued_emails()
    failing_for(minutes=2)

    assert Email.objects.get().error == "The mail server refused the address"
    assert mail_pause() is None


def test_pause_with_nothing_left_to_try_ends_by_itself(anna, server_down):
    accounts.request_sign_in_link(anna.email)
    send_queued_emails()
    failing_for(minutes=2)
    assert mail_pause() is not None

    assert mail_pause(now=timezone.now() + 2 * PROBE_EVERY) is None


def test_pm_sees_the_pause_and_tries_now(client, pm, anna, server_down):
    accounts.request_sign_in_link(anna.email)
    send_queued_emails()
    failing_for(minutes=2)
    client.force_login(pm)

    page = client.get("/logs/emails/").content.decode()
    assert "Emails are paused:" in page
    assert "Try now" in page
    assert 'aria-label="Emails are paused"' in page

    server_down.undo()
    after = client.post("/logs/emails/try/", follow=True).content.decode()

    assert "emails are going out again" in after
    assert "Emails are paused:" not in after
    assert len(mail.outbox) == 1


def test_try_now_while_still_down_says_so(client, pm, anna, server_down):
    accounts.request_sign_in_link(anna.email)
    send_queued_emails()
    failing_for(minutes=2)
    client.force_login(pm)

    page = client.post("/logs/emails/try/", follow=True).content.decode()

    assert "Still not sent" in page


def test_client_cannot_try_now(client, anna):
    client.force_login(anna)

    assert client.post("/logs/emails/try/").status_code == 404


def sent_rows(user: User, count: int) -> None:
    Email.objects.bulk_create(
        Email(
            user=user,
            to=user.email,
            kind=EmailKind.SIGN_IN_LINK,
            subject="Link",
            status=EmailStatus.SENT,
        )
        for _ in range(count)
    )


def test_counter_counts_emails_sent_today_only(anna):
    sent_rows(anna, 3)
    Email.objects.filter(pk=Email.objects.first().pk).update(
        tried_at=timezone.now() - timedelta(days=2)
    )
    Email.objects.create(
        user=anna,
        to=anna.email,
        kind=EmailKind.SIGN_IN_LINK,
        subject="Link",
        status=EmailStatus.FAILED,
        error="Mail server unreachable",
    )

    assert sent_today().sent == 2


def test_counter_is_never_capped_at_the_limit(client, pm, anna, settings):
    settings.EMAIL_DAILY_LIMIT = 100
    sent_rows(anna, 3721)
    client.force_login(pm)

    page = client.get("/logs/emails/").content.decode()

    assert "3721" in page
    assert "of 100" in page
    assert "email-count-over" in page
