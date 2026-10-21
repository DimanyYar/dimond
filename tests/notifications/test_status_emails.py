from datetime import timedelta
from io import StringIO

import pytest
from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import Company, Notify, User
from apps.notifications.models import Email, EmailKind
from apps.notifications.services import GROUPING, send_request_updates
from apps.requests import services
from apps.requests.models import ClientStatus, Request, RequestType, StatusEvent, WhenNeeded

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", email_domain="atlas-crm.com", owner=pm)


def person(atlas: Company, email: str, name: str, notify: Notify, signed_in: bool = True) -> User:
    user = User.objects.create_user(email, name=name, company=atlas, notify=notify)
    if signed_in:
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
    return user


@pytest.fixture
def anna(atlas) -> User:
    return person(atlas, "anna.k@atlas-crm.com", "Anna K.", Notify.OWN)


@pytest.fixture
def mark(atlas) -> User:
    return person(atlas, "mark.t@atlas-crm.com", "Mark T.", Notify.COMPANY)


@pytest.fixture
def olga(atlas) -> User:
    return person(atlas, "olga.p@atlas-crm.com", "Olga P.", Notify.NONE)


@pytest.fixture
def feature(anna) -> Request:
    return services.create_request(
        anna,
        RequestType.FEATURE,
        "Lead card: show the source campaign",
        why="Sales asks where a lead came from",
        when_needed=WhenNeeded.NOT_SURE,
    )


def settle() -> int:
    """Let the 3 minutes pass and run the worker's round."""
    return send_request_updates(now=timezone.now() + GROUPING + timedelta(seconds=1))


def sent_to() -> set[str]:
    return {address for message in mail.outbox for address in message.to}


def html(message) -> str:
    return message.alternatives[0][0]


def test_status_change_from_jira_emails_the_author_and_people_who_follow_the_team(
    feature, anna, mark, olga
):
    services.change_status(feature, ClientStatus.IN_PROGRESS, None)

    assert settle() == 2
    assert sent_to() == {"anna.k@atlas-crm.com", "mark.t@atlas-crm.com"}
    message = mail.outbox[0]
    assert message.subject == (f"{feature.key} · Lead card: show the source campaign — in progress")
    assert "The team started on it" in html(message)
    assert "The team started on it" in message.body


def test_nothing_goes_out_before_three_minutes(feature, anna):
    services.change_status(feature, ClientStatus.IN_PROGRESS, None)

    assert send_request_updates(now=timezone.now() + GROUPING - timedelta(seconds=5)) == 0
    assert mail.outbox == []


def test_changes_of_one_request_within_three_minutes_make_one_email(feature, anna, pm):
    services.change_status(feature, ClientStatus.PLANNED, pm)
    services.change_status(feature, ClientStatus.IN_PROGRESS, None)

    assert settle() == 1
    message = mail.outbox[0]
    assert message.subject.endswith("— in progress")
    assert "Also in the last few minutes" in html(message)
    assert "It’s planned" in html(message)


def test_a_change_late_in_the_window_does_not_hold_the_email_back(feature, anna, pm):
    services.change_status(feature, ClientStatus.PLANNED, pm)
    StatusEvent.objects.update(created_at=timezone.now() - timedelta(minutes=2))
    services.change_status(feature, ClientStatus.IN_PROGRESS, None)

    assert send_request_updates(now=timezone.now() + timedelta(minutes=1, seconds=1)) == 1
    assert mail.outbox[0].subject.endswith("— in progress")


def test_each_change_is_emailed_once(feature, anna):
    services.change_status(feature, ClientStatus.IN_PROGRESS, None)
    settle()

    assert settle() == 0
    assert len(mail.outbox) == 1


def test_my_requests_leaves_out_a_colleagues_request(atlas, anna, mark):
    marks = services.create_request(
        mark, RequestType.IDEA, "Dark mode", why="Night shifts", when_needed=WhenNeeded.NOT_SURE
    )
    services.change_status(marks, ClientStatus.IN_PROGRESS, None)

    settle()

    assert sent_to() == {"mark.t@atlas-crm.com"}


def test_invited_person_who_never_signed_in_gets_nothing(atlas, feature):
    person(atlas, "ruslan.v@atlas-crm.com", "", Notify.COMPANY, signed_in=False)
    services.change_status(feature, ClientStatus.IN_PROGRESS, None)

    settle()

    assert "ruslan.v@atlas-crm.com" not in sent_to()


def test_person_without_access_gets_nothing(feature, anna, mark):
    mark.is_active = False
    mark.save()
    services.change_status(feature, ClientStatus.IN_PROGRESS, None)

    settle()

    assert sent_to() == {"anna.k@atlas-crm.com"}


def test_decline_names_the_pm_but_not_the_reason(feature, anna, pm):
    services.decline_request(pm, feature, "Out of scope for this contract")

    settle()

    message = mail.outbox[0]
    assert "Dmitry declined this request" in html(message)
    assert "Out of scope" not in html(message)
    assert "Out of scope" not in message.body


def test_later_is_emailed(feature, anna, pm):
    services.move_to_later(pm, feature)

    settle()

    assert "Dmitry moved it to Later" in html(mail.outbox[0])


def test_client_who_withdrew_gets_no_email_but_the_team_watcher_does(feature, anna, mark):
    services.withdraw_request(anna, feature)

    settle()

    assert sent_to() == {"mark.t@atlas-crm.com"}
    assert "Anna K. withdrew this request" in html(mail.outbox[0])


def test_pm_question_sends_no_status_email(feature, anna, pm):
    services.post_message(pm, feature, "Which campaigns?", ask_for_input=True)

    assert settle() == 0


def test_client_answer_moving_the_status_back_sends_no_status_email(feature, anna, mark, pm):
    services.post_message(pm, feature, "Which campaigns?", ask_for_input=True)
    settle()
    services.post_message(anna, feature, "Google Ads only")

    assert settle() == 0


def test_new_request_sends_no_status_email(feature, mark):
    assert settle() == 0


def test_email_links_to_the_request_and_to_account_settings(feature, anna):
    services.change_status(feature, ClientStatus.READY_TO_CHECK, None)

    settle()

    page = html(mail.outbox[0])
    assert f"{settings.SITE_URL}{feature.get_absolute_url()}" in page
    assert f"{settings.SITE_URL}/account/" in page
    assert "You get these emails for requests you created." in page


def test_team_watcher_is_told_why_they_get_it(feature, mark):
    services.change_status(feature, ClientStatus.DONE, None)

    settle()

    to_mark = next(m for m in mail.outbox if m.to == ["mark.t@atlas-crm.com"])
    assert "for every request from Atlas" in html(to_mark)


def test_update_is_written_in_the_journal(feature, anna):
    services.change_status(feature, ClientStatus.IN_PROGRESS, None)

    settle()

    email = Email.objects.get()
    assert email.kind == EmailKind.REQUEST_UPDATE
    assert email.subject.endswith("— in progress")


def test_update_the_mail_server_refused_goes_out_on_a_later_round(feature, anna, monkeypatch):
    def refuse(*args, **kwargs):
        raise ConnectionRefusedError

    services.change_status(feature, ClientStatus.IN_PROGRESS, None)
    monkeypatch.setattr("apps.notifications.services.EmailMessage.send", refuse)
    assert settle() == 0
    monkeypatch.undo()

    assert settle() == 1


def test_worker_command_sends_settled_updates(feature, anna):
    services.change_status(feature, ClientStatus.IN_PROGRESS, None)
    StatusEvent.objects.update(created_at=timezone.now() - GROUPING - timedelta(seconds=1))

    call_command("send_emails", stdout=StringIO())

    assert sent_to() == {"anna.k@atlas-crm.com"}
