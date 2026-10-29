"""Subjects are built from names and titles people typed: whatever they hold, the emails go out
once and the queue keeps moving (PRD: email notifications)."""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.models import Company, User
from apps.notifications.models import SUBJECT_LENGTH, Email
from apps.notifications.services import GROUPING, send_request_updates
from apps.requests import services
from apps.requests.models import Message, Request, RequestType, StatusEvent, WhenNeeded

pytestmark = pytest.mark.django_db


def client_of(company: Company) -> User:
    return User.objects.create_user("mark.t@atlas-crm.com", name="Mark T.", company=company)


def new_request(author: User, title: str) -> Request:
    return services.create_request(
        author, RequestType.BUG, title, why="Sales export weekly", when_needed=WhenNeeded.HOTFIX
    )


def settle() -> int:
    return send_request_updates(now=timezone.now() + GROUPING + timedelta(seconds=1))


def nothing_left_to_email() -> bool:
    return not (
        StatusEvent.objects.filter(emailed=False).exists()
        or Message.objects.filter(emailed=False).exists()
    )


def test_a_line_break_in_a_title_does_not_stop_the_emails(pm):
    # The form makes titles one line; this one came in before that, or by hand.
    mark = client_of(Company.objects.create(name="Atlas", owner=pm))
    broken = new_request(mark, "Export fails\nBcc: someone@example.com")
    next_one = new_request(mark, "Leads export times out")

    assert settle() == 2

    titles = {message.subject.split(" — ")[0] for message in mail.outbox}
    assert titles == {
        f"Atlas · {broken.key} · Export fails Bcc: someone@example.com",
        f"Atlas · {next_one.key} · Leads export times out",
    }
    assert nothing_left_to_email()


def test_a_subject_too_long_for_the_journal_is_cut_and_sent_once(pm):
    mark = client_of(Company.objects.create(name="A" * 120, owner=pm))
    new_request(mark, "T" * 200)

    assert settle() == 1
    assert settle() == 0

    [message] = mail.outbox
    assert len(message.subject) == SUBJECT_LENGTH
    assert message.subject.endswith("…")
    assert Email.objects.get().subject == message.subject
    assert nothing_left_to_email()
