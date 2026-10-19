import pytest
from django.conf import settings as django_settings
from django.core.management import CommandError, call_command

from apps.accounts.models import User
from apps.tracker.base import TrackerNotConfigured
from apps.tracker.connection import current_tracker
from apps.tracker.jira import JiraTracker
from apps.tracker.mock import MockTracker
from apps.tracker.models import TrackerKind, TrackerSettings

pytestmark = pytest.mark.django_db

ADMIN_PAGE = f"/{django_settings.ADMIN_URL}tracker/trackersettings/1/change/"


@pytest.fixture
def jira_in_env(settings):
    settings.TRACKER_JIRA_URL = "https://j.shakuro.com"
    settings.TRACKER_JIRA_USERNAME = "dsmirnov"
    settings.TRACKER_JIRA_PASSWORD = "very-secret-password"


@pytest.fixture
def admin_client(client):
    client.force_login(User.objects.create_superuser("dmitry@example.com", password="s3cret-pass"))
    TrackerSettings.load()  # what opening "Tracker settings" in the admin does first
    return client


def use(tracker: str) -> None:
    TrackerSettings.objects.update_or_create(pk=1, defaults={"tracker": tracker})


def test_a_fresh_install_uses_the_demo_tracker():
    assert isinstance(current_tracker(), MockTracker)


def test_jira_is_used_once_chosen_in_the_admin(jira_in_env):
    use(TrackerKind.JIRA)

    assert isinstance(current_tracker(), JiraTracker)


def test_jira_chosen_without_env_settings_says_what_is_missing(settings):
    settings.TRACKER_JIRA_URL = ""
    use(TrackerKind.JIRA)

    with pytest.raises(TrackerNotConfigured, match="TRACKER_JIRA_URL"):
        current_tracker()


def test_admin_refuses_jira_until_env_has_its_address(admin_client, settings):
    settings.TRACKER_JIRA_URL = ""

    response = admin_client.post(ADMIN_PAGE, {"tracker": TrackerKind.JIRA})

    assert "Fill in TRACKER_JIRA_URL" in response.content.decode()
    assert TrackerSettings.load().tracker == TrackerKind.DEMO


def test_admin_switches_to_jira_and_never_shows_the_password(admin_client, jira_in_env):
    response = admin_client.post(ADMIN_PAGE, {"tracker": TrackerKind.JIRA})
    assert response.status_code == 302
    assert TrackerSettings.load().tracker == TrackerKind.JIRA

    page = admin_client.get(ADMIN_PAGE).content.decode()
    assert "https://j.shakuro.com as dsmirnov" in page
    assert "very-secret-password" not in page


def test_there_is_only_ever_one_settings_row(admin_client):
    TrackerSettings.load()
    TrackerSettings(tracker=TrackerKind.DEMO).save()

    assert TrackerSettings.objects.count() == 1
    assert admin_client.get(f"/{django_settings.ADMIN_URL}tracker/trackersettings/").url == (
        ADMIN_PAGE
    )


def test_tracker_check_lists_projects_and_open_tasks(capsys):
    call_command("tracker_check", "--project", "FI", "--find", "lead")

    out = capsys.readouterr().out
    assert "Tracker: Demo" in out
    assert "FI · Atlas" in out
    assert "FI-2208  In Progress · Story · Lead form: validate phone numbers" in out
    assert "FI-2150" not in out  # done tasks are not offered


def test_tracker_check_explains_a_missing_task():
    with pytest.raises(CommandError, match="There is no task FI-1"):
        call_command("tracker_check", "--issue", "FI-1")


def test_tracker_check_explains_missing_jira_settings(settings):
    settings.TRACKER_JIRA_URL = ""
    use(TrackerKind.JIRA)

    with pytest.raises(CommandError, match="empty in .env"):
        call_command("tracker_check")
