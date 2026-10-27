"""A topic per Jira task in the client's Discord forum (PRD: Discord for the team; ADR 0024).
Discord is the fake_discord stand-in from tests/conftest.py, never the real one."""

from datetime import timedelta

import httpx
import pytest
from django.utils import timezone

from apps.accounts.models import Company, User
from apps.notifications.models import DiscordPost, DiscordPostKind, DiscordPostStatus
from apps.notifications.services import send_discord_posts
from apps.requests import services
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded

pytestmark = pytest.mark.django_db

WEBHOOK = "https://discord.com/api/webhooks/1290000000000000001/abcDEF-123_xyz"


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(
        name="Atlas", owner=pm, jira_project="FI", discord_webhook=WEBHOOK
    )


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")


def make(author: User, title: str = "Duplicate leads from the web form", why: str = "") -> Request:
    return services.create_request(
        author,
        RequestType.BUG,
        title,
        why=why or "Managers call the same person twice. @everyone",
        description="Every submission creates two leads.",
        link="https://crm.atlas.test/leads/48390",
        when_needed=WhenNeeded.HOTFIX,
    )


def create_task(pm: User, request: Request) -> Request:
    return services.create_jira_task(
        pm, request, issue_type="Bug", summary=request.title, description="", reply=""
    )


def test_creating_a_task_opens_a_topic_in_the_clients_forum(pm, anna, fake_discord):
    planned = create_task(pm, make(anna))

    assert send_discord_posts() == 1

    [post] = fake_discord.posts
    assert str(post.url).startswith(WEBHOOK)
    assert post.url.params["wait"] == "true"
    [body] = fake_discord.bodies()
    assert body["thread_name"] == f"{planned.jira_key} · Duplicate leads from the web form"
    assert "**Bug** · Hotfix" in body["content"]
    assert "Managers call the same person twice." in body["content"]
    assert f"Jira: {planned.jira_url}" in body["content"]
    planned.refresh_from_db()
    assert planned.discord_topic == fake_discord.TOPIC
    assert DiscordPost.objects.get().status == DiscordPostStatus.SENT


def test_a_clients_words_never_ping_anyone_in_discord(pm, anna, fake_discord):
    create_task(pm, make(anna))
    send_discord_posts()

    [body] = fake_discord.bodies()
    assert body["allowed_mentions"] == {"parse": []}


def test_linking_an_existing_task_opens_a_topic_too(pm, anna, fake_discord):
    services.link_jira_task(pm, make(anna), "FI-2231", "")
    send_discord_posts()

    [body] = fake_discord.bodies()
    assert body["thread_name"].startswith("FI-2231 · ")


def test_a_client_without_discord_gets_nothing(pm, anna, atlas, fake_discord):
    atlas.discord_webhook = ""
    atlas.save()

    create_task(pm, make(anna))

    assert send_discord_posts() == 0
    assert not DiscordPost.objects.exists()
    assert not fake_discord.posts


def test_long_titles_and_reasons_fit_discords_limits(pm, anna, fake_discord):
    create_task(pm, make(anna, title="Leads " * 33, why="Because " * 400))
    send_discord_posts()

    [body] = fake_discord.bodies()
    assert len(body["thread_name"]) <= 100
    assert body["thread_name"].endswith("…")
    assert len(body["content"]) <= 2000


def test_discord_being_down_never_stops_the_task_and_the_post_is_tried_again(
    pm, anna, fake_discord
):
    fake_discord.answers = [httpx.ConnectError("no route")]
    planned = create_task(pm, make(anna))
    start = timezone.now()

    assert send_discord_posts(start) == 0

    planned.refresh_from_db()
    assert planned.status == ClientStatus.PLANNED
    post = DiscordPost.objects.get()
    assert (post.status, post.attempts, post.error) == (
        DiscordPostStatus.WAITING,
        1,
        "Discord unreachable",
    )
    assert send_discord_posts(start + timedelta(seconds=30)) == 0
    assert send_discord_posts(start + timedelta(minutes=1)) == 1
    post.refresh_from_db()
    assert (post.status, post.attempts) == (DiscordPostStatus.SENT, 2)


def test_too_many_posts_wait_as_long_as_discord_asks(pm, anna, fake_discord):
    fake_discord.answers = [httpx.Response(429, json={"retry_after": 120.5, "global": False})]
    create_task(pm, make(anna))
    start = timezone.now()

    send_discord_posts(start)

    post = DiscordPost.objects.get()
    assert post.status == DiscordPostStatus.WAITING
    assert post.next_try_at == start + timedelta(seconds=120.5)


def test_a_deleted_webhook_is_not_tried_again(pm, anna, fake_discord):
    fake_discord.answers = [httpx.Response(404, json={"message": "Unknown Webhook", "code": 10015})]
    create_task(pm, make(anna))

    send_discord_posts()
    send_discord_posts(timezone.now() + timedelta(hours=2))

    post = DiscordPost.objects.get()
    assert (post.status, post.attempts) == (DiscordPostStatus.FAILED, 1)
    assert post.error == "The webhook was deleted in Discord"
    assert post.error_detail == "404 Unknown Webhook"


def test_a_post_that_keeps_failing_gives_up_after_a_day(pm, anna, fake_discord):
    fake_discord.answers = [httpx.Response(502, text="Bad gateway")]
    create_task(pm, make(anna))

    send_discord_posts(timezone.now() + timedelta(days=1, minutes=1))

    post = DiscordPost.objects.get()
    assert post.status == DiscordPostStatus.FAILED
    assert post.error == "Gave up after a day of tries"


def test_turning_discord_off_drops_posts_still_waiting(pm, anna, atlas, fake_discord):
    create_task(pm, make(anna))
    atlas.discord_webhook = ""
    atlas.save()

    send_discord_posts()

    post = DiscordPost.objects.get()
    assert post.status == DiscordPostStatus.FAILED
    assert post.error == "Discord was turned off for this client"
    assert not fake_discord.posts


def test_a_new_task_for_the_request_gets_a_topic_of_its_own(pm, anna, fake_discord):
    request = create_task(pm, make(anna))
    send_discord_posts()
    services.drop_deleted_task(request)

    services.link_jira_task(pm, request, "FI-2231", "")

    request.refresh_from_db()
    assert request.discord_topic == ""
    assert DiscordPost.objects.filter(kind=DiscordPostKind.TASK, task_key="FI-2231").exists()
