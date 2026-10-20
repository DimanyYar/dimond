"""The Jira sync brings task statuses back to requests (PRD: statuses and Jira mapping, Jira
sync; ADR 0019). A fake tracker answers; no test reaches Jira."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import Company, User
from apps.files.models import Attachment
from apps.requests import services
from apps.requests.models import ClientStatus, Request, RequestType, TaskEvent, WhenNeeded
from apps.requests.selectors import PmReason, pm_reasons, request_page, requests_for
from apps.tracker.base import Issue, IssueNotFound, TrackerUnreachable
from apps.tracker.models import SyncEvent, SyncOutcome, SyncRun
from apps.tracker.services import sync_tasks

pytestmark = pytest.mark.django_db


class FakeJira:
    """Answers with the statuses a test sets; a key it does not know is a deleted task, and
    `moved` maps an old key to the task Jira answers with when asked for it by that key."""

    def __init__(self, statuses: dict[str, str], fails: Exception | None = None) -> None:
        self.statuses = statuses
        self.fails = fails
        self.moved: dict[str, Issue] = {}
        self.asked: list[list[str]] = []

    def issue(self, key: str) -> Issue:
        if key in self.moved:
            return self.moved[key]
        if key in self.statuses:
            return Issue(key, "", self.statuses[key], "Story", "FI", False, "")
        raise IssueNotFound(key)

    def issues(self, keys: list[str]) -> list[Issue]:
        self.asked.append(keys)
        if self.fails:
            raise self.fails
        return [
            Issue(key, "", self.statuses[key], "Story", "FI", self.statuses[key] == "Done", "")
            for key in keys
            if key in self.statuses
        ]


@pytest.fixture
def jira(monkeypatch) -> FakeJira:
    fake = FakeJira({})
    monkeypatch.setattr("apps.tracker.services.current_tracker", lambda: fake)
    return fake


@pytest.fixture
def anna(pm) -> User:
    atlas = Company.objects.create(name="Atlas", owner=pm, jira_project="FI")
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")


def linked(
    author: User,
    key: str,
    status: ClientStatus = ClientStatus.PLANNED,
    jira_status: str = "To Do",
) -> Request:
    request = services.create_request(
        author,
        RequestType.IMPROVEMENT,
        f"Request for {key}",
        "Context",
        WhenNeeded.NEXT_RELEASE,
    )
    Request.objects.filter(pk=request.pk).update(
        jira_key=key, jira_status=jira_status, status=status
    )
    request.refresh_from_db()
    return request


def status_of(request: Request) -> ClientStatus:
    request.refresh_from_db()
    return ClientStatus(request.status)


def test_a_status_change_in_jira_reaches_the_client(jira, anna):
    request = linked(anna, "FI-2214")
    jira.statuses["FI-2214"] = "Code review"

    run = sync_tasks()

    assert status_of(request) == ClientStatus.IN_PROGRESS
    event = request.status_events.last()
    assert (event.from_status, event.to_status, event.actor) == ("planned", "in_progress", None)
    entries = [e.event for e in request_page(anna, request.pk).thread if e.event]
    assert "In progress — the team is working on it" in entries
    [logged] = run.events.all()
    assert (logged.task_key, logged.what) == ("FI-2214", "To Do → Code review")
    assert logged.effect == "Client status: Planned → In progress."
    assert run.outcome == SyncOutcome.SUCCEEDED
    assert run.checked == 1


def test_every_mapped_jira_status_shows_the_client_status_from_the_prd(jira, anna):
    expected = {
        "In Progress": ClientStatus.IN_PROGRESS,
        "Test Ready": ClientStatus.IN_PROGRESS,
        "Staging Ready": ClientStatus.IN_PROGRESS,
        "on Staging": ClientStatus.READY_TO_CHECK,
        "Production Ready": ClientStatus.READY_TO_CHECK,
        "on Production": ClientStatus.DONE,
        "Done": ClientStatus.DONE,
    }
    requests = {}
    for n, jira_status in enumerate(expected):
        requests[jira_status] = linked(anna, f"FI-{100 + n}")
        jira.statuses[f"FI-{100 + n}"] = jira_status

    sync_tasks()

    assert {s: status_of(r) for s, r in requests.items()} == expected


def test_done_in_jira_closes_the_request_and_its_public_links(jira, anna):
    request = linked(anna, "FI-2190", ClientStatus.READY_TO_CHECK, "on Staging")
    video = Attachment.objects.create(
        request=request,
        uploaded_by=anna,
        file="form.mp4",
        name="form.mp4",
        content_type="video/mp4",
        size=10,
        public_token="t" * 43,
    )
    assert video.is_public(timezone.now())
    jira.statuses["FI-2190"] = "on Production"

    run = sync_tasks()

    assert status_of(request) == ClientStatus.DONE
    video.refresh_from_db()
    assert not video.is_public(timezone.now())
    assert run.events.get().effect == "Client status: Ready to check → Done. The request is closed."


def test_a_task_sent_back_in_jira_does_not_move_the_client_back(jira, anna):
    request = linked(anna, "FI-2190", ClientStatus.READY_TO_CHECK, "on Staging")
    jira.statuses["FI-2190"] = "In Progress"

    run = sync_tasks()

    assert status_of(request) == ClientStatus.READY_TO_CHECK
    logged = run.events.get()
    assert logged.what == "on Staging → In Progress"
    assert logged.effect == "Went back in Jira; the client keeps seeing “Ready to check”."
    request.refresh_from_db()
    assert request.jira_status == "In Progress"


def test_an_unknown_status_keeps_the_client_status_and_needs_attention(jira, anna):
    request = linked(anna, "FI-2207", ClientStatus.IN_PROGRESS, "In Progress")
    jira.statuses["FI-2207"] = "Blocked"

    run = sync_tasks()

    assert status_of(request) == ClientStatus.IN_PROGRESS
    logged = run.events.get()
    assert logged.needs_attention
    assert logged.what == "Unknown status “Blocked”"
    assert logged.effect == "“Blocked” isn't mapped, so the client keeps “In progress”."
    assert run.outcome == SyncOutcome.PROBLEMS


@pytest.mark.parametrize("closed", [ClientStatus.DECLINED, ClientStatus.WITHDRAWN])
def test_declined_and_withdrawn_requests_are_never_touched(jira, anna, closed):
    request = linked(anna, "FI-2214", closed)
    jira.statuses["FI-2214"] = "Done"

    run = sync_tasks()

    assert status_of(request) == closed
    assert run.events.get().effect.endswith("in dimond; Jira goes its own way.")


def test_a_question_to_the_client_waits_for_the_answer(jira, anna):
    # A request with a task waits for the client once the team asks from Jira (#17).
    request = linked(anna, "FI-2214", ClientStatus.NEEDS_INPUT, "In Progress")
    jira.statuses["FI-2214"] = "on Staging"

    sync_tasks()
    assert status_of(request) == ClientStatus.NEEDS_INPUT

    services.post_message(anna, request, "Webinars and ads.")
    sync_tasks()
    assert status_of(request) == ClientStatus.READY_TO_CHECK


def test_later_moves_only_once_the_task_is_in_work(jira, anna):
    request = linked(anna, "FI-2197", ClientStatus.LATER, "")
    jira.statuses["FI-2197"] = "To Do"

    sync_tasks()
    assert status_of(request) == ClientStatus.LATER

    jira.statuses["FI-2197"] = "In Progress"
    sync_tasks()
    assert status_of(request) == ClientStatus.IN_PROGRESS


def test_a_reopened_task_leaves_the_done_request_closed(jira, anna):
    request = linked(anna, "FI-2150", ClientStatus.DONE, "Done")
    jira.statuses["FI-2150"] = "In Progress"

    run = sync_tasks()

    assert status_of(request) == ClientStatus.DONE
    assert run.events.get().effect == "Reopened in Jira; the request stays closed."


def test_requests_closed_long_ago_are_not_read(jira, anna):
    linked(anna, "FI-2150", ClientStatus.DONE, "Done")
    Request.objects.update(updated_at=timezone.now() - timedelta(days=31))

    sync_tasks()

    assert jira.asked == [[]]


def test_a_run_with_no_news_logs_nothing(jira, anna):
    linked(anna, "FI-2214")
    jira.statuses["FI-2214"] = "Code review"
    sync_tasks()

    second = sync_tasks()

    assert second.outcome == SyncOutcome.SUCCEEDED
    assert not second.events.exists()
    assert second.checked == 1


def test_jira_not_answering_is_logged_and_changes_nothing(monkeypatch, anna):
    request = linked(anna, "FI-2214")
    failing = FakeJira({"FI-2214": "Code review"}, fails=TrackerUnreachable("Jira timed out"))
    monkeypatch.setattr("apps.tracker.services.current_tracker", lambda: failing)

    run = sync_tasks()

    assert (run.outcome, run.error) == (SyncOutcome.FAILED, "Jira timed out")
    assert status_of(request) == ClientStatus.PLANNED


def test_quiet_runs_are_kept_a_week_and_the_rest_a_year(jira, anna):
    request = linked(anna, "FI-2214")
    now = timezone.now()
    quiet_old = SyncRun.objects.create(outcome=SyncOutcome.SUCCEEDED)
    quiet_new = SyncRun.objects.create(outcome=SyncOutcome.SUCCEEDED)
    busy_old = SyncRun.objects.create(outcome=SyncOutcome.SUCCEEDED)
    SyncEvent.objects.create(run=busy_old, request=request, task_key="FI-2214", what="", effect="")
    failed_old = SyncRun.objects.create(outcome=SyncOutcome.FAILED)
    ancient = SyncRun.objects.create(outcome=SyncOutcome.FAILED)
    SyncRun.objects.filter(pk__in=[quiet_old.pk, busy_old.pk, failed_old.pk]).update(
        started_at=now - timedelta(days=8)
    )
    SyncRun.objects.filter(pk=quiet_new.pk).update(started_at=now - timedelta(days=6))
    SyncRun.objects.filter(pk=ancient.pk).update(started_at=now - timedelta(days=366))

    sync_tasks()

    kept = set(SyncRun.objects.values_list("pk", flat=True))
    assert {quiet_new.pk, busy_old.pk, failed_old.pk} <= kept
    assert not {quiet_old.pk, ancient.pk} & kept


def test_the_demo_tracker_brings_its_statuses_too(anna, pm, capsys):
    request = services.create_request(
        anna, RequestType.IMPROVEMENT, "Filter by source", "Context", WhenNeeded.NEXT_RELEASE
    )
    services.link_jira_task(pm, request, "FI-2214", reply="", comment=False)
    assert status_of(request) == ClientStatus.PLANNED

    call_command("sync_tracker")

    assert status_of(request) == ClientStatus.IN_PROGRESS
    assert SyncEvent.objects.get().what == "Status in Jira: Code review"
    assert "Succeeded: 1 task(s), 1 change(s)" in capsys.readouterr().out


def moved_task(key: str, status: str = "on Staging") -> Issue:
    return Issue(key, "", status, "Story", key.split("-")[0], False, f"https://jira.test/{key}")


def test_a_task_deleted_in_jira_is_unlinked_with_a_line_and_waits_for_the_pm(jira, anna, pm):
    request = linked(anna, "FI-2207", ClientStatus.IN_PROGRESS, "In Progress")

    run = sync_tasks()

    request.refresh_from_db()
    assert (request.jira_key, request.lost_jira_key) == ("", "FI-2207")
    assert request.status == ClientStatus.IN_PROGRESS
    lines = [e.event for e in request_page(pm, request.pk).thread if e.event]
    assert "FI-2207 unlinked: deleted in Jira. Link another task or decline it" in lines
    logged = run.events.get()
    assert (logged.task_key, logged.what, logged.needs_attention) == (
        "FI-2207",
        "Not found in Jira",
        True,
    )
    assert pm_reasons(requests_for(pm)) == {request.pk: PmReason.TASK_LOST}


def test_the_pm_links_another_task_to_a_request_whose_task_was_deleted(jira, anna, pm):
    request = linked(anna, "FI-2207", ClientStatus.IN_PROGRESS, "In Progress")
    sync_tasks()

    services.link_jira_task(pm, request, "FI-2214", "")

    request.refresh_from_db()
    assert (request.jira_key, request.lost_jira_key) == ("FI-2214", "")
    assert request.pk not in pm_reasons(requests_for(pm))


def test_a_deleted_task_is_noticed_once(jira, anna):
    linked(anna, "FI-2207", ClientStatus.IN_PROGRESS, "In Progress")
    sync_tasks()

    again = sync_tasks()

    assert not again.events.exists()


def test_a_task_gone_after_the_request_closed_changes_nothing(jira, anna):
    request = linked(anna, "FI-2150", ClientStatus.DONE, "Done")

    run = sync_tasks()

    request.refresh_from_db()
    assert request.jira_key == "FI-2150"
    assert not run.events.exists()


def test_a_task_moved_within_the_clients_project_is_followed_under_its_new_key(jira, anna):
    request = linked(anna, "FI-2207", ClientStatus.IN_PROGRESS, "In Progress")
    jira.moved["FI-2207"] = moved_task("FI-2290")

    run = sync_tasks()

    request.refresh_from_db()
    assert request.jira_key == "FI-2290"
    assert request.status == ClientStatus.READY_TO_CHECK
    assert [e.what for e in run.events.all()] == [
        "Moved in Jira: FI-2207 → FI-2290",
        "In Progress → on Staging",
    ]
    assert not run.events.filter(needs_attention=True).exists()


def test_a_task_moved_to_another_clients_project_is_no_longer_followed(jira, anna):
    request = linked(anna, "FI-2207", ClientStatus.IN_PROGRESS, "In Progress")
    jira.moved["FI-2207"] = moved_task("NW-90", "Done")

    run = sync_tasks()

    request.refresh_from_db()
    assert (request.jira_key, request.jira_moved_to) == ("FI-2207", "NW-90")
    assert request.status == ClientStatus.IN_PROGRESS
    logged = run.events.get()
    assert logged.needs_attention
    assert logged.effect.startswith("NW-90 is outside Atlas’s project")
    assert not sync_tasks().events.exists()


def test_the_pm_unlinks_a_task_moved_to_another_clients_project(jira, anna, pm):
    request = linked(anna, "FI-2207", ClientStatus.IN_PROGRESS, "In Progress")
    jira.moved["FI-2207"] = moved_task("NW-90")
    sync_tasks()

    services.unlink_task(pm, request)

    request.refresh_from_db()
    assert (request.jira_key, request.jira_moved_to) == ("", "")
    lines = [e.event for e in request_page(pm, request.pk).thread if e.event]
    assert lines[-2:] == [
        "FI-2207 moved to NW-90, another client’s project: the sync stopped following it",
        "Dmitry unlinked FI-2207 (moved to NW-90)",
    ]


def test_jira_not_answering_about_a_missing_task_leaves_it_for_the_next_run(monkeypatch, anna):
    request = linked(anna, "FI-2207", ClientStatus.IN_PROGRESS, "In Progress")

    class Flaky(FakeJira):
        def issue(self, key: str) -> Issue:
            raise TrackerUnreachable("timeout")

    monkeypatch.setattr("apps.tracker.services.current_tracker", lambda: Flaky({}))

    sync_tasks()

    request.refresh_from_db()
    assert request.jira_key == "FI-2207"


def test_the_key_stays_in_the_thread_after_the_task_is_gone_but_only_the_pm_reads_why(
    jira, anna, pm
):
    request = services.create_request(
        anna, RequestType.IMPROVEMENT, "Filter by source", "Context", WhenNeeded.NEXT_RELEASE
    )
    services.link_jira_task(pm, request, "FI-2214", "")
    Request.objects.filter(pk=request.pk).update(jira_key="FI-9999")
    request.refresh_from_db()
    TaskEvent.objects.filter(request=request).update(task_key="FI-9999")

    sync_tasks()

    pm_lines = [e.event for e in request_page(pm, request.pk).thread if e.event]
    client_lines = [e.event for e in request_page(anna, request.pk).thread if e.event]
    assert "Linked to FI-9999" in pm_lines
    assert "Linked to FI-9999" in client_lines
    assert any(line.startswith("FI-9999 unlinked") for line in pm_lines)
    assert not any("unlinked" in line for line in client_lines)
