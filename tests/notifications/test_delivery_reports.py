"""Resend's reports about an email after it took it (ADR 0021): delivered, bounced and marked
as spam, received by a signed webhook."""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone
from svix.webhooks import Webhook

from apps.accounts import services as accounts
from apps.accounts.models import Company, Notify, User
from apps.notifications.models import Email, EmailKind, EmailStatus
from apps.notifications.selectors import sent_today

pytestmark = pytest.mark.django_db

SECRET = "whsec_" + base64.b64encode(b"a test secret of 24 bytes").decode()
URL = "/resend/webhook/"


@pytest.fixture(autouse=True)
def webhook_on(settings):
    settings.ANYMAIL_RESEND_SIGNING_SECRET = SECRET


@pytest.fixture
def mark(pm) -> User:
    atlas = Company.objects.create(name="Atlas", email_domain="atlas-crm.com", owner=pm)
    return User.objects.create_user(
        "mark.t@atlas-crm.com", name="Mark T.", company=atlas, notify=Notify.COMPANY
    )


@pytest.fixture
def email(mark) -> Email:
    return Email.objects.create(
        user=mark,
        to=mark.email,
        kind=EmailKind.REQUEST_UPDATE,
        subject="DMN-1041 · Lead card — the team has a question",
        status=EmailStatus.SENT,
        provider_id="4f1d6c2e-0b7a-4c1e-9d55-1f2e3a4b5c6d",
        tried_at=datetime(2026, 10, 22, 8, 46, 0, tzinfo=UTC),
    )


def report(client, kind: str, email_id: str, at: str = "2026-10-22T08:46:01.400Z", **data):
    body = json.dumps(
        {
            "type": kind,
            "created_at": at,
            "data": {"email_id": email_id, "to": ["mark.t@atlas-crm.com"], **data},
        }
    )
    signed_at = timezone.now()
    signature = Webhook(SECRET).sign("msg_1", signed_at, body)
    return client.post(
        URL,
        body,
        content_type="application/json",
        headers={
            "svix-id": "msg_1",
            "svix-timestamp": str(int(signed_at.timestamp())),
            "svix-signature": signature,
        },
    )


def test_delivered_report_turns_the_email_delivered(client, email):
    assert report(client, "email.delivered", email.provider_id).status_code == 200

    email.refresh_from_db()
    assert email.status == EmailStatus.DELIVERED
    assert email.delivery_seconds == pytest.approx(1.4)


def test_bounce_is_logged_in_plain_words(client, email):
    report(
        client,
        "email.bounced",
        email.provider_id,
        bounce={"message": "The recipient's email provider sent a hard bounce message"},
    )

    email.refresh_from_db()
    assert email.status == EmailStatus.BOUNCED
    assert email.error == "Mailbox doesn’t exist"
    assert "hard bounce" in email.error_detail


def test_bounce_leaves_the_persons_emails_on(client, email, mark):
    report(client, "email.bounced", email.provider_id, bounce={"message": "hard bounce message"})

    mark.refresh_from_db()
    assert mark.notify == Notify.COMPANY


def test_spam_mark_switches_the_person_to_nothing(client, email, mark):
    report(client, "email.complained", email.provider_id, at="2026-10-14T09:00:00Z")

    email.refresh_from_db()
    mark.refresh_from_db()
    assert email.status == EmailStatus.SPAM
    assert mark.notify == Notify.NONE
    assert mark.emails_off_at == datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def test_late_delivered_report_does_not_hide_a_spam_mark(client, email):
    report(client, "email.complained", email.provider_id)
    report(client, "email.delivered", email.provider_id)

    email.refresh_from_db()
    assert email.status == EmailStatus.SPAM


def test_opens_and_clicks_are_not_kept(client, email):
    report(client, "email.opened", email.provider_id)

    email.refresh_from_db()
    assert email.status == EmailStatus.SENT


def test_report_about_an_unknown_email_changes_nothing(client, email):
    assert report(client, "email.complained", "not-ours").status_code == 200

    assert Email.objects.get().status == EmailStatus.SENT


def test_forged_report_is_refused(client, email, mark):
    response = client.post(
        URL,
        json.dumps({"type": "email.complained", "data": {"email_id": email.provider_id}}),
        content_type="application/json",
        headers={"svix-id": "msg_1", "svix-timestamp": "1", "svix-signature": "v1,forged"},
    )

    mark.refresh_from_db()
    assert response.status_code == 400
    assert mark.notify == Notify.COMPANY


def test_webhook_is_off_without_a_secret(client, settings, email):
    settings.ANYMAIL_RESEND_SIGNING_SECRET = ""

    assert report(client, "email.delivered", email.provider_id).status_code == 404


def test_delivered_and_spam_emails_still_count_as_sent_today(email):
    email.tried_at = timezone.now()
    email.status = EmailStatus.SPAM
    email.save()

    assert sent_today().sent == 1


def test_client_sees_why_emails_were_switched_off(client, email, mark):
    report(client, "email.complained", email.provider_id, at="2026-10-14T09:00:00Z")
    client.force_login(mark)

    page = client.get("/account/").content.decode()

    assert "Switched to Nothing automatically on Oct 14" in page


def test_picking_again_clears_the_spam_note(client, email, mark):
    report(client, "email.complained", email.provider_id)
    mark.refresh_from_db()

    accounts.choose_emails(mark, Notify.OWN)

    mark.refresh_from_db()
    assert mark.emails_off_at is None


def test_pm_sees_the_spam_note_on_the_person_page(client, pm, email, mark):
    report(client, "email.complained", email.provider_id, at="2026-10-14T09:00:00Z")
    client.force_login(pm)

    page = client.get(f"/clients/{mark.company_id}/people/{mark.pk}/").content.decode()

    assert "an email to Mark T. was marked as spam" in page


def test_logs_show_the_delivery_reports(client, pm, email, mark):
    bounced = Email.objects.create(
        user=mark,
        to=mark.email,
        kind=EmailKind.INVITE,
        subject="Invite",
        status=EmailStatus.SENT,
        provider_id="b0unce",
        tried_at=timezone.now() - timedelta(minutes=5),
    )
    report(client, "email.delivered", email.provider_id)
    report(client, "email.bounced", bounced.provider_id, bounce={"message": "hard bounce message"})
    client.force_login(pm)

    page = client.get("/logs/emails/").content.decode()

    assert "Delivered" in page
    assert "· 1.4 s" in page
    assert "Invite to Atlas. Mailbox doesn’t exist" in page
