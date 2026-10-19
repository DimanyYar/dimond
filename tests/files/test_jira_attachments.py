"""Attachments go to the Jira task (PRD: attachments to Jira; mockup 5.1 · Create Jira task):
pictures, PDFs and text as files, video and voice as a public link that works while the task
is open and again for a week when the PM shares it. No test reaches Jira."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Company, Role, User
from apps.files.models import Attachment
from apps.files.uploads import check_upload
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded
from apps.requests.services import create_request
from apps.tracker.base import Issue, TrackerRefused
from apps.tracker.mock import MockTracker

from .conftest import MP4, PNG, upload

pytestmark = pytest.mark.django_db


class RecordingTracker(MockTracker):
    def __init__(self, refuse: bool = False) -> None:
        self.refuse = refuse
        self.descriptions: list[str] = []
        self.attached: list[tuple[str, str, bytes]] = []

    def create_issue(
        self, project_key: str, issue_type: str, summary: str, description: str, labels: list[str]
    ) -> Issue:
        self.descriptions.append(description)
        return super().create_issue(project_key, issue_type, summary, description, labels)

    def attach_file(self, key: str, name: str, content: bytes, content_type: str) -> None:
        if self.refuse:
            raise TrackerRefused("Jira said the file is larger than its attachment limit.")
        self.attached.append((key, name, content))


@pytest.fixture
def request_with_files(anna, atlas) -> Request:
    Company.objects.filter(pk=atlas.pk).update(jira_project="FI")
    return create_request(
        anna,
        RequestType.BUG,
        "Duplicate leads",
        "Managers call twice",
        WhenNeeded.HOTFIX,
        link="https://crm.atlas.test",
        files=[
            check_upload(upload("duplicates.png", PNG)),
            check_upload(upload("form-submit.mp4", MP4)),
        ],
    )


def files_of(request: Request) -> tuple[Attachment, Attachment]:
    return request.attachments.get(name="duplicates.png"), request.attachments.get(
        name="form-submit.mp4"
    )


def create(client, pm: User, request: Request, tracker: RecordingTracker, monkeypatch, **ways):
    monkeypatch.setattr("apps.requests.services.current_tracker", lambda: tracker)
    picture, video = files_of(request)
    client.force_login(pm)
    data = {
        "issue_type": "Bug",
        "summary": request.title,
        "description": "h3. What happens",
        f"file_{picture.id.hex}": ways.get("picture", "file"),
        f"file_{video.id.hex}": ways.get("video", "link"),
    }
    return client.post(reverse("request_jira_task", args=[request.pk]), data)


def test_the_window_offers_files_as_files_and_video_as_a_link(client, pm, request_with_files):
    picture, video = files_of(request_with_files)
    client.force_login(pm)

    page = client.get(reverse("request_jira_task", args=[request_with_files.pk])).content.decode()

    assert f'name="file_{picture.id.hex}" value="file" checked' in page
    assert f'name="file_{video.id.hex}" value="link" checked' in page


def test_pictures_go_as_files_and_video_as_a_public_link(
    client, pm, request_with_files, monkeypatch
):
    tracker = RecordingTracker()

    create(client, pm, request_with_files, tracker, monkeypatch)

    request_with_files.refresh_from_db()
    picture, video = files_of(request_with_files)
    assert tracker.attached == [(request_with_files.jira_key, "duplicates.png", PNG)]
    assert "h3. Video and voice" in tracker.descriptions[0]
    assert f"[form-submit.mp4|{video.public_url}]" in tracker.descriptions[0]
    assert video.public_token
    assert not picture.public_token


def test_skipped_files_go_nowhere(client, pm, request_with_files, monkeypatch):
    tracker = RecordingTracker()

    create(client, pm, request_with_files, tracker, monkeypatch, picture="skip", video="skip")

    assert tracker.attached == []
    assert "Video and voice" not in tracker.descriptions[0]


def test_a_file_jira_refuses_leaves_the_task_and_a_note_for_the_pm(
    client, pm, request_with_files, monkeypatch
):
    create(client, pm, request_with_files, RecordingTracker(refuse=True), monkeypatch)

    request_with_files.refresh_from_db()
    assert request_with_files.status == ClientStatus.PLANNED
    note = request_with_files.notes.get()
    assert note.text.startswith(f"Couldn't attach duplicates.png to {request_with_files.jira_key}")
    assert "Attach it in Jira by hand." in note.text


@pytest.fixture
def shared_video(client, pm, request_with_files, monkeypatch) -> Attachment:
    create(client, pm, request_with_files, RecordingTracker(), monkeypatch)
    client.logout()
    return files_of(request_with_files)[1]


def test_the_public_link_plays_without_signing_in_while_the_task_is_open(client, shared_video):
    response = client.get(reverse("public_file", args=[shared_video.public_token]))

    assert response.status_code == 200
    assert response["Content-Type"] == "video/mp4"
    assert response["Referrer-Policy"] == "no-referrer"
    assert "noindex" in response["X-Robots-Tag"]


def test_a_made_up_link_finds_nothing(client):
    assert client.get(reverse("public_file", args=["made-up-token"])).status_code == 404


def test_the_link_closes_with_the_request_and_the_pm_shares_it_for_a_week(client, pm, shared_video):
    url = reverse("public_file", args=[shared_video.public_token])
    Request.objects.filter(pk=shared_video.request_id).update(status=ClientStatus.DONE)
    assert client.get(url).status_code == 404

    client.force_login(pm)
    page = client.get(shared_video.request.get_absolute_url()).content.decode()
    assert "Public link closed with the request" in page
    client.post(reverse("share_file_again", args=[shared_video.id]))
    client.logout()

    assert client.get(url).status_code == 200
    shared_video.refresh_from_db()
    assert shared_video.shared_until - timezone.now() > timedelta(days=6)

    Attachment.objects.filter(pk=shared_video.pk).update(
        shared_until=timezone.now() - timedelta(minutes=1)
    )
    assert client.get(url).status_code == 404


def test_only_the_clients_pm_shares_again(client, anna, shared_video):
    Request.objects.filter(pk=shared_video.request_id).update(status=ClientStatus.DONE)
    other_pm = User.objects.create_user("olga@shakuro.com", name="Olga", role=Role.PM)

    for someone in (anna, other_pm):
        client.force_login(someone)
        response = client.post(reverse("share_file_again", args=[shared_video.id]))
        assert response.status_code in (403, 404)
    shared_video.refresh_from_db()
    assert shared_video.shared_until is None


def test_the_client_never_sees_the_public_link_on_the_card(client, anna, shared_video):
    client.force_login(anna)

    page = client.get(shared_video.request.get_absolute_url()).content.decode()

    assert shared_video.public_token not in page
