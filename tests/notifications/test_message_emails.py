"""Emails about new messages in a request (PRD: email notifications): who wrote and what
kind of message, never its text."""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.models import Company, Notify, User
from apps.notifications.services import GROUPING, send_request_updates
from apps.requests import services
from apps.requests.models import ClientStatus, Message, Request, RequestType, WhenNeeded

pytestmark = pytest.mark.django_db

QUESTION = "Which campaigns should the card show?"


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", email_domain="atlas-crm.com", owner=pm)


def person(atlas: Company, email: str, name: str, notify: Notify) -> User:
    user = User.objects.create_user(email, name=name, company=atlas, notify=notify)
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
def feature(anna) -> Request:
    return services.create_request(
        anna,
        RequestType.FEATURE,
        "Lead card: show the source campaign",
        why="Sales asks where a lead came from",
        when_needed=WhenNeeded.NOT_SURE,
    )


def settle() -> int:
    return send_request_updates(now=timezone.now() + GROUPING + timedelta(seconds=1))


def to(address: str):
    return next(message for message in mail.outbox if message.to == [address])


def html(message) -> str:
    return message.alternatives[0][0]


def test_pm_question_is_emailed_without_its_text(feature, anna, pm):
    services.post_message(pm, feature, QUESTION, ask_for_input=True)

    assert settle() == 1
    email = to("anna.k@atlas-crm.com")
    assert email.subject.endswith("— a question from Dmitry")
    assert "Dmitry has a question" in html(email)
    assert "Needs your input" in html(email)
    assert QUESTION not in html(email)
    assert QUESTION not in email.body


def test_pm_reply_is_emailed_without_its_text(feature, anna, pm):
    services.post_message(pm, feature, "We'll show the UTM campaign")

    settle()

    email = to("anna.k@atlas-crm.com")
    assert "Dmitry replied" in html(email)
    assert "UTM" not in html(email)


def test_team_question_is_emailed_without_its_text(feature, anna):
    Message.objects.create(
        request=feature, text=QUESTION, team_comment_id="10001", team_author="Olga P."
    )

    settle()

    email = to("anna.k@atlas-crm.com")
    assert email.subject.endswith("— the team has a question")
    assert "The team has a question" in html(email)
    assert QUESTION not in html(email)
    assert "Olga" not in html(email)


def test_colleague_message_reaches_the_author_but_not_the_writer(feature, anna, mark):
    services.post_message(mark, feature, "Also the ad group, please")

    assert settle() == 1
    assert "Mark T. wrote in the request" in html(to("anna.k@atlas-crm.com"))


def test_authors_own_message_reaches_only_the_team_watcher(feature, anna, mark):
    services.post_message(anna, feature, "Any news?")

    settle()

    assert [message.to for message in mail.outbox] == [["mark.t@atlas-crm.com"]]


def test_client_answer_to_a_question_is_one_message_email_not_a_status_email(
    feature, anna, mark, pm
):
    services.post_message(pm, feature, QUESTION, ask_for_input=True)
    settle()
    mail.outbox.clear()

    services.post_message(anna, feature, "Google Ads only")
    settle()

    assert [message.to for message in mail.outbox] == [["mark.t@atlas-crm.com"]]
    assert "Anna K. wrote in the request" in html(mail.outbox[0])


def test_message_and_status_change_together_make_one_email(feature, anna, pm):
    services.post_message(pm, feature, "Picked it up for the next release")
    services.change_status(feature, ClientStatus.IN_PROGRESS, None)

    assert settle() == 1
    email = to("anna.k@atlas-crm.com")
    assert email.subject.endswith("— in progress")
    assert "Dmitry replied" in html(email)


def test_internal_note_is_never_emailed(feature, anna, pm):
    services.add_note(pm, feature, "Olga thinks two days")

    assert settle() == 0


def test_each_message_is_emailed_once(feature, anna, pm):
    services.post_message(pm, feature, "Noted")
    settle()

    assert settle() == 0
