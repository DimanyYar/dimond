"""The client's default sprint for new tasks, set on the Clients page (PRD: Jira sync, "Create
task"; roadmap #29). Sprints come from the demo tracker: FI has open sprints, NW has none."""

import pytest
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.tracker.base import TrackerUnreachable
from apps.tracker.mock import MockTracker

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm, jira_project="FI")


def card(client, company: Company) -> str:
    return client.get(reverse("client", args=[company.pk]) + "?sprint").content.decode()


def choose(client, company: Company, sprint: str):
    return client.post(reverse("change_default_sprint", args=[company.pk]), {"sprint": sprint})


def test_a_new_client_puts_tasks_in_the_backlog(client, pm, atlas):
    client.force_login(pm)

    page = client.get(reverse("client", args=[atlas.pk])).content.decode()

    assert "New tasks: Backlog" in page


def test_pm_chooses_one_of_the_projects_open_sprints(client, pm, atlas):
    client.force_login(pm)
    page = card(client, atlas)
    assert '<option value="" selected>Backlog</option>' in page
    assert '<option value="102">Bugs bucket</option>' in page

    response = choose(client, atlas, "102")

    assert response.url == reverse("client", args=[atlas.pk])
    atlas.refresh_from_db()
    assert (atlas.jira_sprint_id, atlas.jira_sprint_name) == (102, "Bugs bucket")
    page = client.get(reverse("client", args=[atlas.pk])).content.decode()
    assert "New tasks: sprint Bugs bucket" in page


def test_backlog_can_be_chosen_again(client, pm, atlas):
    atlas.jira_sprint_id, atlas.jira_sprint_name = 102, "Bugs bucket"
    atlas.save()
    client.force_login(pm)

    choose(client, atlas, "")

    atlas.refresh_from_db()
    assert (atlas.jira_sprint_id, atlas.jira_sprint_name) == (None, "")


def test_a_sprint_of_another_project_is_not_saved(client, pm, atlas):
    client.force_login(pm)

    response = choose(client, atlas, "201")

    assert "This sprint is closed or gone from Jira" in response.content.decode()
    atlas.refresh_from_db()
    assert atlas.jira_sprint_id is None


def test_with_several_boards_the_board_name_comes_first(client, pm):
    harbor = Company.objects.create(name="Harbor Foods", owner=pm, jira_project="HF")
    client.force_login(pm)

    assert '<option value="202">HF mobile · Mobile bucket</option>' in card(client, harbor)


def test_a_project_without_a_board_offers_only_the_backlog(client, pm):
    northwind = Company.objects.create(name="Northwind", owner=pm, jira_project="NW")
    client.force_login(pm)

    assert "No scrum board with open sprints" in card(client, northwind)


def test_a_closed_default_sprint_is_flagged_and_kept_until_the_pm_changes_it(client, pm, atlas):
    atlas.jira_sprint_id, atlas.jira_sprint_name = 999, "Summer bucket"
    atlas.save()
    client.force_login(pm)

    page = card(client, atlas)

    assert "Default sprint “Summer bucket” is closed or gone from Jira" in page
    assert '<option value="" selected>Backlog</option>' in page
    atlas.refresh_from_db()
    assert atlas.jira_sprint_id == 999


def test_jira_not_answering_still_lets_the_pm_choose_the_backlog(client, pm, atlas, monkeypatch):
    class Silent(MockTracker):
        def open_sprints(self, project_key):
            raise TrackerUnreachable("Jira at https://j.shakuro.com did not answer.")

    monkeypatch.setattr("apps.tracker.forms.current_tracker", Silent)
    client.force_login(pm)

    assert "load the sprints: Jira at https://j.shakuro.com did not answer." in card(client, atlas)
    choose(client, atlas, "")
    atlas.refresh_from_db()
    assert atlas.jira_sprint_id is None


def test_a_new_jira_project_starts_from_the_backlog(client, pm, atlas):
    atlas.jira_sprint_id, atlas.jira_sprint_name = 102, "Bugs bucket"
    atlas.save()
    client.force_login(pm)

    client.post(reverse("change_jira_project", args=[atlas.pk]), {"jira_project": "HF"})

    atlas.refresh_from_db()
    assert (atlas.jira_project, atlas.jira_sprint_id, atlas.jira_sprint_name) == ("HF", None, "")


def test_saving_the_same_jira_project_keeps_the_sprint(client, pm, atlas):
    atlas.jira_sprint_id, atlas.jira_sprint_name = 102, "Bugs bucket"
    atlas.save()
    client.force_login(pm)

    client.post(reverse("change_jira_project", args=[atlas.pk]), {"jira_project": "FI"})

    atlas.refresh_from_db()
    assert atlas.jira_sprint_id == 102


def test_pm_cannot_change_another_pms_client(client, pm):
    other_pm = User.objects.create_user("olga@shakuro.com", name="Olga", role=Role.PM)
    theirs = Company.objects.create(name="Northwind", owner=other_pm, jira_project="FI")
    client.force_login(pm)

    assert choose(client, theirs, "102").status_code == 404
    theirs.refresh_from_db()
    assert theirs.jira_sprint_id is None
