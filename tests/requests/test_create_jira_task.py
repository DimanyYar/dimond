"""The PM creates a Jira task from a request (PRD: "Create task", PM buttons; mockup 5.1 ·
Create Jira task). Tasks go to the demo tracker or a recording stand-in, never to Jira."""

import pytest
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.requests import services
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded
from apps.tracker.base import Issue, TrackerRefused, TrackerUnreachable
from apps.tracker.mock import MockTracker
from apps.tracker.models import TrackerSettings

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm, jira_project="FI")


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")


@pytest.fixture
def bug(anna) -> Request:
    return services.create_request(
        anna,
        RequestType.BUG,
        "Duplicate leads from the web form",
        why="Managers call the same person twice.",
        description="Every submission creates two leads.",
        steps="1. Send the form\n2. Open Leads",
        link="https://crm.atlas.test/leads/48390",
        when_needed=WhenNeeded.HOTFIX,
    )


class RecordingTracker(MockTracker):
    """The demo tracker that also remembers what it was asked to create."""

    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    def create_issue(
        self, project_key: str, issue_type: str, summary: str, description: str, labels: list[str]
    ) -> Issue:
        self.created.append(
            {
                "project": project_key,
                "type": issue_type,
                "summary": summary,
                "description": description,
                "labels": labels,
            }
        )
        return super().create_issue(project_key, issue_type, summary, description, labels)


@pytest.fixture
def tracker(monkeypatch) -> RecordingTracker:
    recording = RecordingTracker()
    monkeypatch.setattr("apps.requests.services.current_tracker", lambda: recording)
    return recording


def window(client, user: User, request: Request):
    client.force_login(user)
    return client.get(reverse("request_jira_task", args=[request.pk]))


def create(client, user: User, request: Request, **fields):
    client.force_login(user)
    data = {
        "issue_type": "Bug",
        "summary": request.title,
        "description": "h3. What happens",
        "reply": "on",
        "reply_text": "Thanks, the team has it.",
    }
    return client.post(reverse("request_jira_task", args=[request.pk]), data | fields)


def test_the_window_is_prefilled_from_the_request(client, pm, bug):
    page = window(client, pm, bug).content.decode()

    assert "Project <strong>FI · Atlas</strong>" in page
    assert '<option value="Bug" selected>Bug</option>' in page
    assert 'value="Duplicate leads from the web form"' in page
    assert "h3. Steps to reproduce\n1. Send the form" in page
    assert f"dimond: [{bug.key}|http://localhost:8000/requests/{bug.pk}/]" in page


def test_anything_but_a_bug_starts_as_a_story(client, pm, anna):
    idea = services.create_request(
        anna, RequestType.IDEA, "Bulk SMS", why="Context", when_needed=WhenNeeded.NOT_SURE
    )

    assert (
        '<option value="Story" selected>Story</option>' in window(client, pm, idea).content.decode()
    )


def test_pm_creates_the_task_in_the_clients_project_and_the_request_is_planned(
    client, pm, bug, tracker
):
    response = create(client, pm, bug)

    bug.refresh_from_db()
    assert response.url == bug.get_absolute_url()
    assert bug.status == ClientStatus.PLANNED
    assert bug.jira_key.startswith("FI-")
    assert bug.jira_url == f"https://jira.example.com/browse/{bug.jira_key}"
    assert tracker.created == [
        {
            "project": "FI",
            "type": "Bug",
            "summary": bug.title,
            "description": "h3. What happens",
            "labels": ["dimond"],
        }
    ]
    assert bug.status_events.last().actor == pm


def test_the_thread_says_planned_with_the_task_key_and_the_reply(client, pm, anna, bug, tracker):
    create(client, pm, bug)
    bug.refresh_from_db()

    client.force_login(anna)
    page = client.get(bug.get_absolute_url()).content.decode()
    assert "Planned" in page
    assert f"Linked to {bug.jira_key}" in page
    assert "Thanks, the team has it." in page


def test_unticked_reply_posts_nothing_to_the_client(client, pm, bug, tracker):
    create(client, pm, bug, reply="", reply_text="")

    assert not bug.messages.exists()


def test_a_ticked_reply_needs_words(client, pm, bug, tracker):
    response = create(client, pm, bug, reply_text="  ")

    assert "Write the reply, or untick" in response.content.decode()
    assert tracker.created == []


def test_jira_saying_no_keeps_the_window_and_changes_nothing(client, pm, bug, monkeypatch):
    class Refusing(MockTracker):
        def create_issue(self, *args: object) -> Issue:
            raise TrackerRefused("Jira said no: Component/s is required.")

    monkeypatch.setattr("apps.requests.services.current_tracker", Refusing)

    response = create(client, pm, bug, summary="My own summary")

    page = response.content.decode()
    assert response.status_code == 422
    assert "Jira said no: Component/s is required." in page
    assert 'value="My own summary"' in page
    bug.refresh_from_db()
    assert (bug.status, bug.jira_key) == (ClientStatus.RECEIVED, "")


def test_a_request_with_a_task_gets_no_second_one(client, pm, bug, tracker):
    create(client, pm, bug)

    assert create(client, pm, bug).status_code == 403
    assert len(tracker.created) == 1


def test_only_the_clients_own_pm_creates_tasks(client, anna, bug, tracker):
    other_pm = User.objects.create_user("olga@shakuro.com", name="Olga", role=Role.PM)

    assert create(client, anna, bug).status_code in (403, 404)
    assert create(client, other_pm, bug).status_code == 404
    assert tracker.created == []


def test_the_card_offers_the_button_only_while_a_task_can_be_made(client, pm, bug, tracker):
    client.force_login(pm)
    assert "Create Jira task" in client.get(bug.get_absolute_url()).content.decode()

    create(client, pm, bug)

    assert "Create Jira task" not in client.get(bug.get_absolute_url()).content.decode()


def test_without_a_jira_project_the_card_says_where_to_choose_one(client, pm, atlas, bug):
    Company.objects.filter(pk=atlas.pk).update(jira_project="")
    client.force_login(pm)

    page = client.get(bug.get_absolute_url()).content.decode()

    assert "choose Atlas’s Jira project" in page
    assert reverse("request_jira_task", args=[bug.pk]) not in page


def test_client_sees_the_task_key_and_opens_it_only_when_allowed(client, pm, anna, bug, tracker):
    create(client, pm, bug)
    bug.refresh_from_db()
    client.force_login(anna)

    page = client.get(bug.get_absolute_url()).content.decode()
    assert f"<dd>{bug.jira_key}</dd>" in page
    assert bug.jira_url not in page

    TrackerSettings.objects.update_or_create(pk=1, defaults={"clients_see_jira_links": True})
    assert f'href="{bug.jira_url}"' in client.get(bug.get_absolute_url()).content.decode()


def test_a_broken_connection_warns_that_the_task_may_exist(client, pm, bug, monkeypatch):
    class Silent(MockTracker):
        def create_issue(self, *args: object) -> Issue:
            raise TrackerUnreachable("Jira at https://j.shakuro.com did not answer: timed out")

    monkeypatch.setattr("apps.requests.services.current_tracker", Silent)

    page = create(client, pm, bug).content.decode()

    assert f"Jira may still have created the task: search FI for {bug.key}" in page
    bug.refresh_from_db()
    assert bug.jira_key == ""


def test_demo_keys_never_repeat_a_key_already_given():
    first = MockTracker().create_issue("FI", "Bug", "One", "", ["dimond"])
    second = MockTracker().create_issue("FI", "Bug", "Two", "", ["dimond"])

    assert first.key != second.key


def test_the_window_chooses_the_clients_default_sprint(client, pm, atlas, bug):
    atlas.jira_sprint_id, atlas.jira_sprint_name = 102, "Bugs bucket"
    atlas.save()

    page = window(client, pm, bug).content.decode()

    assert '<option value="102" selected>Bugs bucket</option>' in page
    assert '<option value="">Backlog</option>' in page


def test_the_task_goes_into_the_sprint_the_pm_picked(client, pm, bug, tracker, monkeypatch):
    moved: list[tuple[str, int]] = []
    monkeypatch.setattr(tracker, "move_to_sprint", lambda key, sprint: moved.append((key, sprint)))

    create(client, pm, bug, sprint="103")

    bug.refresh_from_db()
    assert moved == [(bug.jira_key, 103)]
    assert not bug.notes.exists()


def test_backlog_moves_the_task_nowhere(client, pm, bug, tracker, monkeypatch):
    moved: list[tuple[str, int]] = []
    monkeypatch.setattr(tracker, "move_to_sprint", lambda key, sprint: moved.append((key, sprint)))

    create(client, pm, bug, sprint="")

    bug.refresh_from_db()
    assert bug.status == ClientStatus.PLANNED
    assert moved == []


def test_a_sprint_jira_refuses_leaves_the_task_in_the_backlog_with_a_note(client, pm, bug, tracker):
    create(client, pm, bug, sprint="104")  # the demo's sprint closed a moment ago

    bug.refresh_from_db()
    assert bug.status == ClientStatus.PLANNED
    [note] = bug.notes.all()
    assert note.text.startswith(f"Couldn't put {bug.jira_key} into sprint Old bucket: Jira said no")
    assert "It is in the backlog; move it in Jira by hand." in note.text


def test_a_sprint_closed_before_sending_is_refused_before_any_task_is_made(
    client, pm, bug, tracker
):
    response = create(client, pm, bug, sprint="999")

    assert "This sprint is closed or gone from Jira" in response.content.decode()
    assert tracker.created == []


def test_without_sprints_from_jira_the_task_still_goes_to_the_backlog(
    client, pm, bug, tracker, monkeypatch
):
    class Silent(MockTracker):
        def open_sprints(self, project_key):
            raise TrackerUnreachable("Jira at https://j.shakuro.com did not answer.")

    monkeypatch.setattr("apps.tracker.forms.current_tracker", Silent)
    page = window(client, pm, bug).content.decode()
    assert "load the sprints: Jira at https://j.shakuro.com did not answer." in page
    assert "Tasks go to the backlog." in page

    create(client, pm, bug, sprint="")

    bug.refresh_from_db()
    assert bug.jira_key.startswith("FI-")


def test_a_sprint_move_without_an_answer_asks_the_pm_to_check_jira(
    client, pm, bug, tracker, monkeypatch
):
    def silent(key, sprint):
        raise TrackerUnreachable("Jira at https://j.shakuro.com did not answer.")

    monkeypatch.setattr(tracker, "move_to_sprint", silent)

    create(client, pm, bug, sprint="103")

    bug.refresh_from_db()
    [note] = bug.notes.all()
    assert note.text == (
        f"Couldn't confirm that {bug.jira_key} reached sprint Ideas bucket: Jira at "
        "https://j.shakuro.com did not answer. Check its sprint in Jira."
    )


def test_a_sprint_picked_before_jira_stopped_answering_is_not_called_closed(
    client, pm, bug, tracker, monkeypatch
):
    class Silent(MockTracker):
        def open_sprints(self, project_key):
            raise TrackerUnreachable("Jira at https://j.shakuro.com did not answer.")

    monkeypatch.setattr("apps.tracker.forms.current_tracker", Silent)

    page = create(client, pm, bug, sprint="103").content.decode()

    assert "closed or gone" not in page
    assert "load the sprints: Jira at https://j.shakuro.com did not answer." in page
    assert tracker.created == []
