"""Logs · Jira sync (PRD: Jira sync; mockup 4.x · PM logs): only PMs, each sees their own
clients' events."""

import pytest
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.requests import services
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded
from apps.tracker.models import SyncEvent, SyncOutcome, SyncRun

pytestmark = pytest.mark.django_db


@pytest.fixture
def anna(pm) -> User:
    atlas = Company.objects.create(name="Atlas", owner=pm, jira_project="FI")
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")


@pytest.fixture
def other_pm(db) -> User:
    return User.objects.create_user("olga@shakuro.com", name="Olga", role=Role.PM)


def linked(author: User, key: str, jira_status: str = "To Do") -> Request:
    request = services.create_request(
        author, RequestType.IMPROVEMENT, f"Request {key}", "Context", WhenNeeded.NEXT_RELEASE
    )
    Request.objects.filter(pk=request.pk).update(
        jira_key=key, jira_status=jira_status, status=ClientStatus.PLANNED
    )
    return request


def logs(client, user: User, query: str = "") -> str:
    client.force_login(user)
    response = client.get(reverse("logs") + query)
    assert response.status_code == 200
    return response.content.decode()


def run_with(request: Request, what: str, problem: bool = False, **fields) -> SyncRun:
    run = SyncRun.objects.create(outcome=SyncOutcome.SUCCEEDED, checked=1, **fields)
    SyncRun.objects.filter(pk=run.pk).update(finished_at=run.started_at)
    SyncEvent.objects.create(
        run=run,
        request=request,
        task_key=request.jira_key or "FI-1",
        what=what,
        effect="Client status: Planned → In progress.",
        needs_attention=problem,
    )
    return run


def test_only_a_pm_opens_the_logs(client, anna):
    client.force_login(anna)

    assert client.get(reverse("logs")).status_code == 404


def test_the_pm_sees_a_change_with_the_request_it_moved(client, pm, anna):
    request = linked(anna, "FI-2214")
    run_with(request, "To Do → Code review")

    page = logs(client, pm)

    assert "1 change" in page
    assert "To Do → Code review" in page
    assert "Client status: Planned → In progress." in page
    assert f"{request.key} · Atlas" in page


def test_another_pms_clients_never_show_up(client, anna, other_pm):
    run_with(linked(anna, "FI-2214"), "To Do → Code review")

    page = logs(client, other_pm)

    assert "To Do → Code review" not in page
    assert "No changes" in page


def test_a_failed_run_is_shown_to_every_pm_with_jiras_words(client, other_pm):
    run = SyncRun.objects.create(outcome=SyncOutcome.FAILED, error="Jira did not answer: timeout")
    SyncRun.objects.filter(pk=run.pk).update(finished_at=run.started_at)

    page = logs(client, other_pm, "?show=problems")

    assert "Sync failed" in page
    assert "Jira did not answer: timeout" in page
    assert "Problems · 1" in page


def test_the_newest_run_with_changes_opens_by_itself(client, pm, anna):
    run_with(linked(anna, "FI-2214"), "To Do → Code review")
    quiet = SyncRun.objects.create(outcome=SyncOutcome.SUCCEEDED, checked=1)
    SyncRun.objects.filter(pk=quiet.pk).update(finished_at=quiet.started_at)

    page = logs(client, pm)

    assert page.count("<details open>") == 1
    assert page.index("<details>") < page.index("<details open>")


def test_with_changes_leaves_out_quiet_runs(client, pm, anna):
    quiet = SyncRun.objects.create(outcome=SyncOutcome.SUCCEEDED, checked=14)
    SyncRun.objects.filter(pk=quiet.pk).update(finished_at=quiet.started_at)
    run_with(linked(anna, "FI-2214"), "To Do → Code review")

    assert "14 linked tasks checked" in logs(client, pm)
    assert "14 linked tasks checked" not in logs(client, pm, "?show=changes")


def test_every_pm_page_says_when_jira_last_synced(client, pm):
    assert "Jira not synced yet" in logs(client, pm)

    run = SyncRun.objects.create(outcome=SyncOutcome.SUCCEEDED)
    SyncRun.objects.filter(pk=run.pk).update(finished_at=run.started_at)
    client.force_login(pm)
    assert "Jira synced just now" in client.get(reverse("clients")).content.decode()

    SyncRun.objects.filter(pk=run.pk).update(outcome=SyncOutcome.FAILED)
    assert "Jira sync failed just now" in client.get(reverse("clients")).content.decode()
