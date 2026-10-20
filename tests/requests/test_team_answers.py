"""The client answers the team's questions from Jira on the request card, and the answer goes
to the Jira task as a comment straight away; if it does not get there, the PM sees why (PRD:
Jira sync, team questions; mockup RequestCardTeamAsk; ADR 0010). Questions come in through the
sync with a fake tracker; no test reaches Jira."""

from datetime import UTC, datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Company, User
from apps.requests import services
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded
from apps.requests.selectors import open_team_questions, pm_reasons, request_page, requests_for
from apps.tracker.base import (
    Account,
    Comment,
    Issue,
    TrackerForbidden,
    TrackerUnreachable,
    tags_the_client,
)
from apps.tracker.connection import login_fingerprint
from apps.tracker.models import TrackerKind, TrackerSettings
from apps.tracker.services import sync_tasks

pytestmark = pytest.mark.django_db


class FakeJira:
    def __init__(self) -> None:
        self.status = "Code review"
        self.comments: list[Comment] = []
        self.added: list[tuple[str, str]] = []
        self.refuses: Exception | None = None

    def add_comment(self, key: str, body: str) -> str:
        if self.refuses:
            raise self.refuses
        self.added.append((key, body))
        return f"9{len(self.added)}"

    def account(self) -> Account:
        return Account("dsmirnov", "Dmitry Smirnov")

    def issues(self, keys: list[str]) -> list[Issue]:
        done = self.status == "Done"
        return [
            Issue(key, "", self.status, "Story", "FI", done, "", comments=tuple(self.comments))
            for key in keys
        ]

    def ask(self, text: str) -> None:
        moment = datetime(2026, 10, 20, 8, len(self.comments), tzinfo=UTC)
        self.comments.append(
            Comment(
                id=str(86400 + len(self.comments)),
                author_login="opetrova@shakuro.com",
                author_name="Olga Petrova",
                text=f"#client {text}",
                created=moment,
                updated=moment,
                asks_the_client=True,
            )
        )
        sync_tasks()


@pytest.fixture
def jira(monkeypatch) -> FakeJira:
    fake = FakeJira()
    monkeypatch.setattr("apps.tracker.services.current_tracker", lambda: fake)
    monkeypatch.setattr("apps.requests.services.current_tracker", lambda: fake)
    return fake


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm, jira_project="FI")


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")


@pytest.fixture
def mark(atlas) -> User:
    return User.objects.create_user("mark@atlas.test", company=atlas, name="Mark T.")


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


def test_one_answer_covers_every_open_question_and_returns_the_status(jira, anna, mark):
    request = linked(anna)
    jira.ask("Several sources at once, or one?")
    jira.ask("Should archived sources show?")

    answer = services.answer_team(mark, request, "Several at once; only active sources.")

    assert answer.answers_team
    assert open_team_questions(request) == []
    assert status_of(request) == ClientStatus.IN_PROGRESS


def test_the_answer_returns_ready_to_check_without_walking_back_through_planned(jira, anna):
    request = linked(anna, ClientStatus.READY_TO_CHECK)
    jira.ask("Which browser did you check it in?")

    services.answer_team(anna, request, "Chrome on a Mac.")

    assert status_of(request) == ClientStatus.READY_TO_CHECK
    to_statuses = list(request.status_events.values_list("to_status", flat=True))
    assert to_statuses[-2:] == [ClientStatus.NEEDS_INPUT, ClientStatus.READY_TO_CHECK]


def test_a_new_question_after_an_answer_opens_the_form_again(jira, anna):
    request = linked(anna)
    jira.ask("Several sources at once?")
    services.answer_team(anna, request, "Yes.")

    jira.ask("And should it remember the last choice?")

    assert [q.text for q in open_team_questions(request)] == [
        "And should it remember the last choice?"
    ]
    assert status_of(request) == ClientStatus.NEEDS_INPUT


def test_a_message_to_dimon_is_not_an_answer_to_the_team(jira, anna):
    request = linked(anna)
    jira.ask("Several sources at once?")

    services.post_message(anna, request, "Dimon, can we talk about the price first?")

    assert status_of(request) == ClientStatus.NEEDS_INPUT
    assert len(open_team_questions(request)) == 1


def test_the_pms_own_question_still_returns_to_received_on_any_reply(anna, pm):
    request = services.create_request(
        anna, RequestType.BUG, "Duplicate leads", "Context", WhenNeeded.HOTFIX, link="https://a.b"
    )
    services.post_message(pm, request, "A link to one lead, please?", ask_for_input=True)

    services.post_message(anna, request, "https://crm.atlas.test/leads/48213")

    assert status_of(request) == ClientStatus.RECEIVED


@pytest.mark.parametrize("who", ["pm", "outsider"])
def test_only_the_clients_company_answers_the_team(client, jira, anna, pm, who):
    request = linked(anna)
    jira.ask("Several sources at once?")
    if who == "pm":
        person = pm
    else:
        other = Company.objects.create(name="Northwind", owner=pm, jira_project="NW")
        person = User.objects.create_user("nina@northwind.test", company=other)
    client.force_login(person)

    response = client.post(reverse("request_answer", args=[request.pk]), {"answer": "Hi"})

    assert response.status_code in (403, 404)
    assert status_of(request) == ClientStatus.NEEDS_INPUT


def test_an_empty_answer_is_sent_back_with_a_hint(client, jira, anna):
    request = linked(anna)
    jira.ask("Several sources at once?")
    client.force_login(anna)

    response = client.post(reverse("request_answer", args=[request.pk]), {"answer": ""})

    assert response.status_code == 422
    assert "Write your answer." in response.content.decode()


def test_the_client_sees_the_banner_the_form_and_where_the_work_stopped(client, jira, anna):
    request = linked(anna)
    jira.ask("Several sources at once?")
    client.force_login(anna)

    page = client.get(request.get_absolute_url()).content.decode()

    assert "The team has a question" in page
    assert 'href="#answer"' in page
    assert "Answer the team" in page
    assert "Anything else? This goes to Dimon, your PM." in page
    steps = [s.name for s in request_page(anna, request.pk).progress.steps]
    assert "In progress · waiting for your answer" in steps


def test_after_the_answer_the_form_and_the_banner_are_gone(client, jira, anna):
    request = linked(anna)
    jira.ask("Several sources at once?")
    client.force_login(anna)

    response = client.post(
        reverse("request_answer", args=[request.pk]), {"answer": "Several at once."}
    )
    page = client.get(response.url).content.decode()

    assert "Several at once." in page
    assert "Answer the team" not in page
    assert "The team has a question" not in page


def test_the_pm_sees_the_question_but_no_answer_form(client, jira, anna, pm):
    request = linked(anna)
    jira.ask("Several sources at once?")
    client.force_login(pm)

    page = client.get(request.get_absolute_url()).content.decode()

    assert "Several sources at once?" in page
    assert "Answer the team" not in page


def test_while_the_team_waits_the_answer_button_leads_and_the_message_button_stays_dark(
    client, jira, anna
):
    request = linked(anna)
    jira.ask("Several sources at once?")
    client.force_login(anna)

    page = client.get(request.get_absolute_url()).content.decode()

    assert 'class="btn btn-primary" type="submit">Send answer' in page
    assert 'class="btn btn-dark">Send to Dimon' in page


def test_the_answer_quotes_the_question_mentions_who_asked_and_says_who_answered(
    jira, anna, settings
):
    settings.SITE_URL = "https://dimond.test"
    request = linked(anna)
    jira.ask("Several sources at once, or one?")

    answer = services.answer_team(anna, request, "Several at once.")

    [(key, body)] = jira.added
    assert key == "FI-2214"
    assert body == (
        "{quote}Several sources at once, or one?{quote}\n"
        "[~opetrova@shakuro.com] Anna K. (Atlas) answered via dimond:\n"
        "Several at once.\n"
        f"[{request.key}|https://dimond.test/requests/{request.pk}/]"
    )
    answer.refresh_from_db()
    assert (answer.jira_comment_id, answer.jira_error) == ("91", "")


def test_one_answer_quotes_every_open_question_and_mentions_each_asker_once(jira, anna):
    request = linked(anna)
    jira.ask("Several sources at once?")
    jira.ask("Archived ones too?")

    services.answer_team(anna, request, "Yes, and no.")

    body = jira.added[0][1]
    assert body.count("{quote}") == 4
    assert body.count("[~opetrova@shakuro.com]") == 1


def test_a_question_already_answered_is_not_quoted_again(jira, anna):
    request = linked(anna)
    jira.ask("Several sources at once?")
    services.answer_team(anna, request, "Yes.")
    jira.ask("Should it remember the last choice?")

    services.answer_team(anna, request, "Please.")

    second = jira.added[1][1]
    assert "Several sources" not in second
    assert "{quote}Should it remember the last choice?{quote}" in second


def test_the_clients_words_never_mention_people_or_ask_a_new_question(jira, anna):
    request = linked(anna)
    jira.ask("Which sources?")

    services.answer_team(anna, request, "#client [~boss] {quote}see !x.png!{quote} C:\\temp")

    body = jira.added[0][1]
    assert "\\#client \\[~boss\\] \\{quote\\}see \\!x.png\\!\\{quote\\} C:&#92;temp" in body
    without_quotes = body.split("{quote}")[-1]
    assert not tags_the_client(without_quotes)


def test_the_answer_reaches_the_client_as_passed_to_the_team(client, jira, anna):
    request = linked(anna)
    jira.ask("Which sources?")
    services.answer_team(anna, request, "Only active ones.")
    client.force_login(anna)

    page = client.get(request.get_absolute_url()).content.decode()

    assert "Passed to the team ·" in page


@pytest.mark.parametrize(
    ("failure", "says"),
    [
        (TrackerForbidden("'dsmirnov' has no permission for this in Jira."), "no permission"),
        (TrackerUnreachable("Jira at https://j.shakuro.com did not answer."), "may have reached"),
    ],
)
def test_an_answer_jira_did_not_take_waits_for_the_pm_and_the_client_is_not_told(
    client, jira, anna, pm, failure, says
):
    request = linked(anna)
    jira.ask("Which sources?")
    jira.refuses = failure

    answer = services.answer_team(anna, request, "Only active ones.")

    answer.refresh_from_db()
    assert says in answer.jira_error
    assert answer.jira_comment_id == ""
    assert pm_reasons(requests_for(pm))[request.pk] == "not_sent"
    client.force_login(anna)
    assert "Passed to the team ·" in client.get(request.get_absolute_url()).content.decode()
    client.force_login(pm)
    pm_page = client.get(request.get_absolute_url()).content.decode()
    assert "Not passed to FI-2214:" in pm_page


def test_while_jiras_login_is_paused_the_answer_is_not_tried(jira, anna, settings):
    request = linked(anna)
    jira.ask("Which sources?")
    settings.TRACKER_JIRA_URL = "https://j.shakuro.com"
    TrackerSettings.objects.update_or_create(
        pk=1,
        defaults={
            "tracker": TrackerKind.JIRA,
            "sync_paused_at": timezone.now(),
            "sync_paused_login": login_fingerprint(),
        },
    )

    answer = services.answer_team(anna, request, "Only active ones.")

    assert jira.added == []
    assert "sync is paused" in answer.jira_error


def test_an_answer_that_got_there_does_not_wait_for_the_pm(jira, anna, pm):
    request = linked(anna)
    jira.ask("Which sources?")

    services.answer_team(anna, request, "Only active ones.")

    assert pm_reasons(requests_for(pm))[request.pk] == "replied"
