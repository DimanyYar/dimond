import pytest
from anymail.exceptions import AnymailAPIError
from django.conf import settings

from apps.accounts import services as accounts
from apps.accounts.models import Company, User
from apps.notifications.models import Email, EmailKind, EmailStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def anna(pm) -> User:
    atlas = Company.objects.create(name="Atlas", email_domain="atlas-crm.com", owner=pm)
    return User.objects.create_user("anna.k@atlas-crm.com", name="Anna K.", company=atlas)


@pytest.fixture
def mail_server_down(monkeypatch):
    def refuse(*args, **kwargs):
        raise ConnectionRefusedError

    monkeypatch.setattr("apps.notifications.services.EmailMessage.send", refuse)
    return monkeypatch


def test_sent_sign_in_link_is_written_in_the_journal(anna):
    accounts.request_sign_in_link(anna.email)
    accounts.send_queued_links()

    email = Email.objects.get()
    assert email.user == anna
    assert email.to == "anna.k@atlas-crm.com"
    assert email.kind == EmailKind.SIGN_IN_LINK
    assert email.subject == "Your dimon.d sign-in link"
    assert email.status == EmailStatus.SENT


def test_invite_is_written_in_the_journal_as_an_invite(anna, pm):
    accounts.invite([anna], invited_by=pm)
    accounts.send_queued_links()

    assert Email.objects.get().kind == EmailKind.INVITE


def test_journal_never_holds_the_link(anna):
    accounts.request_sign_in_link(anna.email)
    accounts.send_queued_links()

    email = Email.objects.values().get()
    assert not any(settings.SITE_URL in str(value) for value in email.values())


def test_email_the_server_refused_is_journaled_as_not_sent(anna, mail_server_down):
    accounts.request_sign_in_link(anna.email)

    assert accounts.send_queued_links() == 0

    email = Email.objects.get()
    assert email.status == EmailStatus.FAILED
    assert email.error == "ConnectionRefusedError"


def test_refused_link_goes_out_on_the_next_round_and_both_attempts_are_journaled(
    anna, mail_server_down
):
    accounts.request_sign_in_link(anna.email)
    accounts.send_queued_links()
    mail_server_down.undo()

    assert accounts.send_queued_links() == 1
    assert list(Email.objects.values_list("status", flat=True)) == [
        EmailStatus.SENT,
        EmailStatus.FAILED,
    ]


def test_resend_error_is_journaled_with_its_reason(anna, monkeypatch):
    def resend_refuses(*args, **kwargs):
        raise AnymailAPIError("Resend API response 422: domain is not verified")

    monkeypatch.setattr("apps.notifications.services.EmailMessage.send", resend_refuses)
    accounts.request_sign_in_link(anna.email)
    accounts.send_queued_links()

    assert "domain is not verified" in Email.objects.get().error


def test_journal_keeps_the_id_the_provider_gave_the_email(anna, settings):
    settings.EMAIL_BACKEND = "anymail.backends.test.EmailBackend"
    accounts.request_sign_in_link(anna.email)
    accounts.send_queued_links()

    assert Email.objects.get().provider_id != ""


def test_mailpit_email_has_no_provider_id(anna):
    accounts.request_sign_in_link(anna.email)
    accounts.send_queued_links()

    assert Email.objects.get().provider_id == ""


def test_journal_row_stays_when_a_cancelled_invite_deletes_the_person(anna, pm):
    accounts.invite([anna], invited_by=pm)
    accounts.send_queued_links()

    accounts.cancel_invite(anna)

    email = Email.objects.get()
    assert email.user is None
    assert email.to == "anna.k@atlas-crm.com"


def test_superuser_reads_the_journal_in_the_admin_but_cannot_change_it(client, anna):
    admin = User.objects.create_superuser("root@shakuro.com", password="s3cret-pass")
    client.force_login(admin)
    accounts.request_sign_in_link(anna.email)
    accounts.send_queued_links()
    email = Email.objects.get()

    journal = client.get(f"/{settings.ADMIN_URL}notifications/email/")
    change = client.post(f"/{settings.ADMIN_URL}notifications/email/{email.pk}/change/", {})

    assert journal.status_code == 200
    assert "anna.k@atlas-crm.com" in journal.content.decode()
    assert change.status_code == 403
