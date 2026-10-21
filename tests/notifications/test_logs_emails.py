import pytest

from apps.accounts.models import Company, Role, User
from apps.notifications.models import Email, EmailKind, EmailStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def anna(pm) -> User:
    atlas = Company.objects.create(name="Atlas", owner=pm)
    return User.objects.create_user("anna.k@atlas-crm.com", name="Anna K.", company=atlas)


def journal(user: User, kind: EmailKind, subject: str, **extra) -> Email:
    return Email.objects.create(
        user=user, to=user.email, kind=kind, subject=subject, status=EmailStatus.SENT, **extra
    )


def test_pm_sees_emails_to_their_clients_and_to_themselves(client, pm, anna):
    journal(anna, EmailKind.REQUEST_UPDATE, "DMN-1038 · Lead card — ready to check")
    journal(pm, EmailKind.PM_UPDATE, "Atlas · DMN-1050 · Export — new request")
    client.force_login(pm)

    page = client.get("/logs/emails/").content.decode()

    assert "anna.k@atlas-crm.com" in page
    assert "DMN-1038 · Lead card — ready to check" in page
    assert "To you: Atlas · DMN-1050 · Export — new request" in page


def test_pm_never_sees_emails_to_another_pms_clients(client, pm):
    olga = User.objects.create_user("olga@shakuro.com", role=Role.PM)
    northwind = Company.objects.create(name="Northwind", owner=olga)
    paul = User.objects.create_user("paul@northwind.co", company=northwind)
    journal(paul, EmailKind.SIGN_IN_LINK, "Your dimon.d sign-in link")
    client.force_login(pm)

    assert "paul@northwind.co" not in client.get("/logs/emails/").content.decode()


def test_email_not_sent_shows_why(client, pm, anna):
    Email.objects.create(
        user=anna,
        to=anna.email,
        kind=EmailKind.SIGN_IN_LINK,
        subject="Your dimon.d sign-in link",
        status=EmailStatus.FAILED,
        error="ConnectionRefusedError",
    )
    client.force_login(pm)

    page = client.get("/logs/emails/").content.decode()

    assert "Not sent" in page
    assert "ConnectionRefusedError" in page
    assert "Sign-in link" in page


def test_invite_names_the_client(client, pm, anna):
    journal(anna, EmailKind.INVITE, "You're invited to dimon.d")
    client.force_login(pm)

    assert "Invite to Atlas" in client.get("/logs/emails/").content.decode()


def test_no_emails_yet(client, pm):
    client.force_login(pm)

    assert "No emails yet" in client.get("/logs/emails/").content.decode()


def test_client_gets_not_found(client, anna):
    client.force_login(anna)

    assert client.get("/logs/emails/").status_code == 404


def test_both_logs_tabs_link_to_each_other(client, pm):
    client.force_login(pm)

    jira = client.get("/logs/").content.decode()
    emails = client.get("/logs/emails/").content.decode()

    assert 'href="/logs/emails/"' in jira
    assert 'href="/logs/"' in emails


def test_resend_id_is_shown_whole_to_copy(client, pm, anna):
    journal(
        anna,
        EmailKind.SIGN_IN_LINK,
        "Your dimon.d sign-in link",
        provider_id="4ef9a417-02e9-4d39-ad75-9611e0fcc33c",
    )
    client.force_login(pm)

    assert "4ef9a417-02e9-4d39-ad75-9611e0fcc33c" in client.get("/logs/emails/").content.decode()
