"""Moving a task to another release (#30, 30f; canvas 7.2; D-005): from its row on the Releases
page the PM picks another open release, Hot Fix or no release. dimond changes the fix version in
Jira, on the task and on subtasks with the same one of their own, updates its copy at once, and
a linked request gets its new "Planned for" with a line in its thread, never an email. Undo
takes it back; Logs · Releases lists every move. The demo tracker stands in for Jira."""

from datetime import date

import pytest
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.notifications.models import Email
from apps.requests.models import PlanEvent, Request
from apps.tracker.base import TrackerRefused
from apps.tracker.mock import MockTracker
from apps.tracker.models import ReleaseMove, TrackerTask, TrackerVersion
from apps.tracker.services import copy_releases

pytestmark = pytest.mark.django_db


class RecordingTracker(MockTracker):
    def __init__(self, refuse: frozenset[str] = frozenset()) -> None:
        self.moves: list[tuple[str, str, str]] = []
        self.refuse = refuse

    def move_fix_version(self, key: str, remove_id: str, add_id: str) -> None:
        if key in self.refuse:
            raise TrackerRefused("Jira said no: Field 'fixVersions' cannot be set.")
        super().move_fix_version(key, remove_id, add_id)
        self.moves.append((key, remove_id, add_id))


@pytest.fixture
def jira(monkeypatch) -> RecordingTracker:
    tracker = RecordingTracker()
    monkeypatch.setattr("apps.tracker.services.current_tracker", lambda: tracker)
    return tracker


@pytest.fixture
def atlas(pm) -> Company:
    company = Company.objects.create(name="Atlas", owner=pm, jira_project="FI")
    copy_releases(MockTracker())
    return company


def version(name: str) -> TrackerVersion:
    return TrackerVersion.objects.get(name=name)


def releases_of(key: str) -> list[str]:
    return sorted(v.name for v in TrackerTask.objects.get(key=key).versions.all())


def move(client, key: str, source: str, target: str):
    to = "none" if target == "No release" else str(version(target).pk)
    return client.post(
        reverse("move_task", args=[key]),
        {"client": Company.objects.get(name="Atlas").pk, "from": version(source).pk, "to": to},
        follow=True,
    )


def test_a_task_moves_to_another_release_in_jira_and_on_the_page(client, pm, atlas, jira):
    client.force_login(pm)

    response = move(client, "FI-2208", "26.8.0", "26.9.0")

    assert jira.moves == [("FI-2208", "20130", "20140")]
    assert releases_of("FI-2208") == ["26.9.0"]
    page = response.content.decode()
    assert "Moved: FI-2208 → 26.9.0" in page
    assert "Undo" in page
    saved = ReleaseMove.objects.get()
    assert (saved.from_name, saved.to_name, saved.moved_by) == ("26.8.0", "26.9.0", pm)


def test_the_note_shows_once(client, pm, atlas, jira):
    client.force_login(pm)
    move(client, "FI-2208", "26.8.0", "26.9.0")

    page = client.get(reverse("releases")).content.decode()

    assert "Moved: FI-2208" not in page


def test_subtasks_with_the_same_fix_version_of_their_own_move_along(client, pm, atlas, jira):
    TrackerTask.objects.get(key="FI-2245").versions.add(version("26.8.0"))
    client.force_login(pm)

    move(client, "FI-2208", "26.8.0", "26.9.0")

    # FI-2246 has no fix version of its own, as in FI: Jira is not asked about it.
    assert jira.moves == [("FI-2208", "20130", "20140"), ("FI-2245", "20130", "20140")]
    assert releases_of("FI-2245") == ["26.9.0"]
    assert releases_of("FI-2246") == []
    assert ReleaseMove.objects.get().subtask_keys == ["FI-2245"]


def test_no_release_takes_only_this_fix_version_off(client, pm, atlas, jira):
    TrackerTask.objects.get(key="FI-2208").versions.add(version("Hot Fix"))
    client.force_login(pm)

    move(client, "FI-2208", "26.8.0", "No release")

    assert jira.moves == [("FI-2208", "20130", "")]
    assert releases_of("FI-2208") == ["Hot Fix"]


def test_undo_takes_the_task_back_with_its_subtasks(client, pm, atlas, jira):
    TrackerTask.objects.get(key="FI-2245").versions.add(version("26.8.0"))
    client.force_login(pm)
    move(client, "FI-2208", "26.8.0", "26.9.0")
    first = ReleaseMove.objects.get()

    response = client.post(reverse("undo_move", args=[first.pk]), {"client": atlas.pk}, follow=True)

    assert releases_of("FI-2208") == ["26.8.0"]
    assert releases_of("FI-2245") == ["26.8.0"]
    assert jira.moves[-2:] == [("FI-2208", "20140", "20130"), ("FI-2245", "20140", "20130")]
    page = response.content.decode()
    assert "Moved back: FI-2208 → 26.8.0" in page
    assert first.undone_by.to_name == "26.8.0"


def test_a_move_is_undone_only_once(client, pm, atlas, jira):
    client.force_login(pm)
    move(client, "FI-2208", "26.8.0", "26.9.0")
    first = ReleaseMove.objects.get()
    client.post(reverse("undo_move", args=[first.pk]), {"client": atlas.pk})

    response = client.post(reverse("undo_move", args=[first.pk]), {"client": atlas.pk}, follow=True)

    assert "already moved back" in response.content.decode()
    assert ReleaseMove.objects.count() == 2


def test_when_jira_refuses_nothing_changes(client, pm, atlas, monkeypatch):
    refusing = RecordingTracker(refuse=frozenset({"FI-2208"}))
    monkeypatch.setattr("apps.tracker.services.current_tracker", lambda: refusing)
    client.force_login(pm)

    response = move(client, "FI-2208", "26.8.0", "26.9.0")

    assert releases_of("FI-2208") == ["26.8.0"]
    assert not ReleaseMove.objects.exists()
    assert "FI-2208 stays where it was" in response.content.decode()


def test_a_subtask_jira_refuses_is_named_and_stays(client, pm, atlas, monkeypatch):
    TrackerTask.objects.get(key="FI-2245").versions.add(version("26.8.0"))
    refusing = RecordingTracker(refuse=frozenset({"FI-2245"}))
    monkeypatch.setattr("apps.tracker.services.current_tracker", lambda: refusing)
    client.force_login(pm)

    response = move(client, "FI-2208", "26.8.0", "26.9.0")

    assert releases_of("FI-2208") == ["26.9.0"]
    assert releases_of("FI-2245") == ["26.8.0"]
    assert "Jira did not move FI-2245 along with FI-2208" in response.content.decode()


def test_the_linked_request_gets_the_new_date_and_a_line_but_no_email(client, pm, atlas, jira):
    target = version("26.9.0")
    target.release_date = date(2026, 12, 2)
    target.save()
    anna = User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")
    request = Request.objects.create(company=atlas, author=anna, title="Phones", jira_key="FI-2208")
    client.force_login(pm)

    response = move(client, "FI-2208", "26.8.0", "26.9.0")

    request.refresh_from_db()
    assert request.planned_for == "26.9.0 · Dec 2"
    assert PlanEvent.objects.get(request=request).text == "Planned for 26.9.0 · Dec 2"
    assert not Email.objects.exists()
    assert f"{request.key} follows it" in response.content.decode()


def test_a_released_release_has_no_move_menu(client, pm, atlas):
    client.force_login(pm)

    open_release = client.get(reverse("releases"), {"release": version("26.8.0").pk})
    released = client.get(reverse("releases"), {"release": version("26.7.0").pk})

    assert 'class="move-menu"' in open_release.content.decode()
    assert 'class="move-menu"' not in released.content.decode()


def test_the_menu_offers_open_releases_hot_fix_and_no_release(client, pm, atlas):
    client.force_login(pm)

    page = client.get(reverse("releases"), {"release": version("26.8.0").pk}).content.decode()
    menu = page[page.index('class="move-options"') :]
    menu = menu[: menu.index("</details>")]

    for name in ("26.9.0", "Hot Fix", "No release", "25.8.0", "26.8.5"):
        assert f"<strong>{name}</strong>" in menu
    for name in ("26.8.0", "26.7.0", "Unreleased"):
        assert f"<strong>{name}</strong>" not in menu


def test_another_pm_cannot_move_my_clients_task(client, atlas, jira):
    other = User.objects.create_user("olga@shakuro.com", name="Olga", role=Role.PM)
    client.force_login(other)

    response = client.post(
        reverse("move_task", args=["FI-2208"]),
        {"from": version("26.8.0").pk, "to": version("26.9.0").pk},
    )

    assert response.status_code == 404
    assert jira.moves == []


def test_logs_list_the_moves_of_my_clients_only(client, pm, atlas, jira):
    client.force_login(pm)
    move(client, "FI-2208", "26.8.0", "26.9.0")

    page = client.get(reverse("logs_releases")).content.decode()
    other = User.objects.create_user("olga@shakuro.com", name="Olga", role=Role.PM)
    client.force_login(other)
    their_page = client.get(reverse("logs_releases")).content.decode()

    assert "26.8.0 → 26.9.0 · Lead form: validate phone numbers" in page
    assert "FI-2208" not in their_page
