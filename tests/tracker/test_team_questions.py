"""The team asks the client from Jira with a comment tagged #client (PRD: Jira sync, team
questions; ADR 0010). A fake tracker answers; no test reaches Jira."""

from datetime import UTC, datetime, timedelta

import pytest

from apps.accounts.models import Company, User
from apps.files.uploads import MAX_VIDEO_SIZE
from apps.requests import services
from apps.requests.models import ClientStatus, Message, Request, RequestType, WhenNeeded
from apps.requests.selectors import (
    can_ask_for_input,
    can_ask_to_withdraw,
    can_edit_request,
    can_withdraw_now,
    pm_reasons,
    request_page,
    requests_for,
    with_unread,
)
from apps.tracker.base import (
    Account,
    Comment,
    Issue,
    TaskFile,
    TrackerRefused,
    TrackerUnreachable,
)
from apps.tracker.mock import DEMO_PICTURE
from apps.tracker.models import SyncOutcome
from apps.tracker.services import sync_tasks

pytestmark = pytest.mark.django_db

ASKED = datetime(2026, 10, 20, 8, 44, tzinfo=UTC)
SCREENSHOT = TaskFile("41555", "lead-sources.png", "image/png", len(DEMO_PICTURE))


def comment(
    text: str = "#client Which sources should the list show?",
    id: str = "86412",
    files: tuple[TaskFile, ...] = (),
    missing: tuple[str, ...] = (),
    updated: datetime = ASKED,
) -> Comment:
    return Comment(
        id=id,
        author_login="opetrova@shakuro.com",
        author_name="Olga Petrova",
        text=text,
        created=ASKED,
        updated=updated,
        files=files,
        missing_files=missing,
        asks_the_client="#client" in text,
    )


class FakeJira:
    """Answers with the task's status and comments a test sets, and with the files' bytes."""

    def __init__(self) -> None:
        self.status = "Code review"
        self.comments: list[Comment] = []
        self.files: dict[str, bytes | Exception] = {SCREENSHOT.id: DEMO_PICTURE}

    def account(self) -> Account:
        return Account("dsmirnov", "Dmitry Smirnov")

    def issues(self, keys: list[str]) -> list[Issue]:
        done = self.status == "Done"
        return [
            Issue(key, "", self.status, "Story", "FI", done, "", comments=tuple(self.comments))
            for key in keys
        ]

    def file_content(self, file: TaskFile) -> bytes:
        found = self.files[file.id]
        if isinstance(found, Exception):
            raise found
        return found


@pytest.fixture
def jira(monkeypatch) -> FakeJira:
    fake = FakeJira()
    monkeypatch.setattr("apps.tracker.services.current_tracker", lambda: fake)
    return fake


@pytest.fixture
def anna(pm) -> User:
    atlas = Company.objects.create(name="Atlas", owner=pm, jira_project="FI")
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")


def linked(author: User, status: ClientStatus = ClientStatus.IN_PROGRESS) -> Request:
    request = services.create_request(
        author,
        RequestType.IMPROVEMENT,
        "Filter leads by source",
        "Context",
        WhenNeeded.NEXT_RELEASE,
    )
    Request.objects.filter(pk=request.pk).update(
        jira_key="FI-2214", jira_status="Code review", status=status
    )
    request.refresh_from_db()
    return request


def status_of(request: Request) -> ClientStatus:
    request.refresh_from_db()
    return ClientStatus(request.status)


def test_a_comment_tagged_client_reaches_the_client_as_a_question_from_the_team(jira, anna):
    request = linked(anna)
    jira.comments = [comment("#client Which sources should the list show?")]

    run = sync_tasks()

    question = request.messages.get()
    assert (question.author, question.text) == (None, "Which sources should the list show?")
    assert (question.team_comment_id, question.team_author) == ("86412", "Olga Petrova")
    assert status_of(request) == ClientStatus.NEEDS_INPUT
    [logged] = run.events.all()
    assert logged.what == "Question for the client · #client"
    assert logged.effect == (
        f"Olga Petrova asked: “Which sources should the list show?” Shown to Atlas in "
        f"{request.key}; In progress → Needs your input."
    )
    assert run.outcome == SyncOutcome.SUCCEEDED


def test_a_comment_without_the_tag_never_reaches_the_client(jira, anna):
    request = linked(anna)
    jira.comments = [comment("@dsmirnov archived sources are still in the settings table")]

    sync_tasks()

    assert not request.messages.exists()
    assert status_of(request) == ClientStatus.IN_PROGRESS


def test_the_tag_in_the_middle_of_a_sentence_is_taken_out_of_the_question(jira, anna):
    request = linked(anna)
    jira.comments = [comment("Quick one #client  — several sources at once?")]

    sync_tasks()

    assert request.messages.get().text == "Quick one — several sources at once?"


def test_each_question_is_copied_once_however_often_the_sync_runs(jira, anna):
    request = linked(anna)
    jira.comments = [comment()]

    sync_tasks()
    second = sync_tasks()

    assert request.messages.count() == 1
    assert not second.events.exists()


def test_two_questions_in_one_run_both_reach_the_client(jira, anna):
    request = linked(anna)
    jira.comments = [comment(id="1"), comment("#client And the order?", id="2")]

    sync_tasks()

    assert [m.text for m in request.messages.all()] == [
        "Which sources should the list show?",
        "And the order?",
    ]
    assert request.status_events.filter(to_status=ClientStatus.NEEDS_INPUT).count() == 1


def test_an_edit_in_jira_rewords_the_question_and_keeps_the_status(jira, anna):
    request = linked(anna)
    jira.comments = [comment()]
    sync_tasks()

    jira.comments = [
        comment("#client Only sources in use, or archived too?", updated=ASKED + timedelta(1))
    ]
    run = sync_tasks()

    assert request.messages.get().text == "Only sources in use, or archived too?"
    assert status_of(request) == ClientStatus.NEEDS_INPUT
    assert run.events.get().what == "Question edited in Jira"


def test_a_question_deleted_in_jira_stays_with_the_client(jira, anna):
    request = linked(anna)
    jira.comments = [comment()]
    sync_tasks()

    jira.comments = []
    sync_tasks()

    assert request.messages.count() == 1


@pytest.mark.parametrize(
    "status", [ClientStatus.READY_TO_CHECK, ClientStatus.LATER, ClientStatus.PLANNED]
)
def test_a_question_moves_any_open_request_to_needs_your_input(jira, anna, status):
    request = linked(anna, status)
    jira.comments = [comment()]

    sync_tasks()

    assert status_of(request) == ClientStatus.NEEDS_INPUT


@pytest.mark.parametrize(
    "status", [ClientStatus.DONE, ClientStatus.DECLINED, ClientStatus.WITHDRAWN]
)
def test_a_question_opens_a_closed_request_again(jira, anna, status):
    request = linked(anna, status)
    jira.comments = [comment()]

    run = sync_tasks()

    assert status_of(request) == ClientStatus.NEEDS_INPUT
    assert run.events.get().effect.endswith(f"{status.label} → Needs your input.")


def test_while_the_client_has_not_answered_the_sync_keeps_waiting(jira, anna):
    request = linked(anna)
    jira.comments = [comment()]
    sync_tasks()

    jira.status = "on Staging"
    sync_tasks()

    assert status_of(request) == ClientStatus.NEEDS_INPUT


def test_the_questions_picture_is_shown_to_the_client(jira, anna):
    request = linked(anna)
    jira.comments = [comment(files=(SCREENSHOT,))]

    sync_tasks()

    [picture] = request.messages.get().attachments.all()
    assert (picture.name, picture.content_type, picture.uploaded_by) == (
        "lead-sources.png",
        "image/png",
        None,
    )


def test_files_the_client_cannot_see_are_named_in_the_question_and_the_log(jira, anna):
    request = linked(anna)
    gone = TaskFile("1", "old.png", "image/png", 10)
    page = TaskFile("2", "report.html", "text/html", 20)
    huge = TaskFile("3", "demo.mov", "video/quicktime", MAX_VIDEO_SIZE + 1)
    jira.files |= {"1": TrackerRefused("old.png is gone."), "2": b"<html><script></script>"}
    jira.comments = [comment(files=(gone, page, huge, SCREENSHOT), missing=("elsewhere.png",))]

    run = sync_tasks()

    question = request.messages.get()
    assert question.files_not_shown == ["elsewhere.png", "old.png", "report.html", "demo.mov"]
    assert [f.name for f in question.attachments.all()] == ["lead-sources.png"]
    assert run.events.get().effect.endswith(
        "Not shown to the client: elsewhere.png, old.png, report.html, demo.mov."
    )


def test_jira_silent_on_a_file_saves_nothing_and_the_next_run_brings_the_question(jira, anna):
    request = linked(anna)
    jira.comments = [comment(files=(SCREENSHOT,))]
    jira.files[SCREENSHOT.id] = TrackerUnreachable("Jira did not answer")

    failed = sync_tasks()

    assert failed.outcome == SyncOutcome.FAILED
    assert not request.messages.exists()
    jira.files[SCREENSHOT.id] = DEMO_PICTURE
    sync_tasks()
    assert request.messages.get().attachments.count() == 1


def test_the_client_and_the_pm_see_the_question_as_new(jira, anna, pm):
    request = linked(anna)
    jira.comments = [comment()]

    sync_tasks()

    for person in (anna, pm):
        assert with_unread(requests_for(person), person).get(pk=request.pk).unread == 1
        assert request_page(person, request.pk).new_count == 1


def test_a_team_question_does_not_put_the_request_in_the_pms_needs_you(jira, anna, pm):
    request = linked(anna)
    jira.comments = [comment()]

    sync_tasks()

    assert pm_reasons(requests_for(pm)).get(request.pk) is None


def test_a_team_question_does_not_hide_a_clients_reply_from_the_pm(jira, anna, pm):
    request = linked(anna)
    services.post_message(anna, request, "Also, can “Unknown” be at the bottom?")
    jira.comments = [comment()]

    sync_tasks()

    assert pm_reasons(requests_for(pm))[request.pk] == "replied"


def test_the_thread_shows_the_team_not_the_developers_name(client, jira, anna):
    request = linked(anna)
    jira.comments = [comment(files=(SCREENSHOT,), missing=("old-filter.png",))]
    sync_tasks()
    client.force_login(anna)

    page = client.get(request.get_absolute_url()).content.decode()

    assert "Which sources should the list show?" in page
    assert "asked in Jira" in page
    assert "Olga" not in page
    assert "A file from the team couldn’t be shown here: old-filter.png" in page


def test_a_question_has_either_an_author_or_its_jira_comment(anna):
    request = linked(anna)

    with pytest.raises(Exception, match="message_has_an_author_or_a_jira_comment"):
        Message.objects.create(request=request, author=None, text="Who wrote this?")


def test_the_demo_task_from_the_mockups_asks_its_question(anna, pm):
    request = services.create_request(
        anna, RequestType.IMPROVEMENT, "Filter by source", "Context", WhenNeeded.NEXT_RELEASE
    )
    services.link_jira_task(pm, request, "FI-2214", reply="", comment=False)

    sync_tasks()

    question = request.messages.get()
    assert question.text.startswith("Which sources should the list show")
    assert question.attachments.get().name == "lead-sources.png"
    assert status_of(request) == ClientStatus.NEEDS_INPUT


def test_a_question_on_a_task_does_not_let_the_client_rewrite_or_drop_the_request(jira, anna):
    request = linked(anna)
    jira.comments = [comment()]
    sync_tasks()
    request.refresh_from_db()

    assert not can_edit_request(anna, request)
    assert not can_withdraw_now(anna, request)
    assert can_ask_to_withdraw(anna, request)


def test_while_the_team_waits_the_pm_does_not_ask_a_question_of_their_own(jira, anna, pm):
    request = linked(anna)
    jira.comments = [comment()]
    sync_tasks()
    request.refresh_from_db()

    assert not can_ask_for_input(pm, request)
