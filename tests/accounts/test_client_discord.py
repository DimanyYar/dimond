"""The client's Discord on the Clients page and Logs · Discord (PRD: Discord for the team;
ADR 0024). Discord is the fake_discord stand-in from tests/conftest.py."""

import httpx
import pytest
from django.urls import reverse

from apps.accounts.models import Company, Role, User
from apps.notifications.models import DiscordPost, DiscordPostKind, DiscordPostStatus

pytestmark = pytest.mark.django_db

WEBHOOK = "https://discord.com/api/webhooks/1290000000000000001/abcDEF-123_xyzQ"


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm, jira_project="FI")


@pytest.fixture
def other_pm(db) -> User:
    return User.objects.create_user("olga@shakuro.com", name="Olga", role=Role.PM)


def save(client, pm: User, company: Company, webhook: str):
    client.force_login(pm)
    return client.post(
        reverse("change_discord_webhook", args=[company.pk]), {"webhook": webhook}, follow=True
    )


def test_pm_saves_a_webhook_and_it_is_never_shown_back(client, pm, atlas):
    page = save(client, pm, atlas, f"  {WEBHOOK} ").content.decode()

    atlas.refresh_from_db()
    assert atlas.discord_webhook == WEBHOOK
    assert "Webhook saved" in page
    assert "…/xyzQ" in page
    assert WEBHOOK not in page
    assert "abcDEF" not in client.get(reverse("client", args=[atlas.pk])).content.decode()


def test_the_clients_page_says_whether_discord_is_on(client, pm, atlas):
    client.force_login(pm)
    assert "No Discord" in client.get(reverse("client", args=[atlas.pk])).content.decode()

    save(client, pm, atlas, WEBHOOK)

    assert "Discord on" in client.get(reverse("client", args=[atlas.pk])).content.decode()


@pytest.mark.parametrize(
    "address",
    [
        "https://evil.test/api/webhooks/1/abc",
        "http://discord.com/api/webhooks/1/abc",
        "https://discord.com/channels/1/2",
        "https://discord.com.evil.test/api/webhooks/1/abc",
    ],
)
def test_only_a_discord_webhook_is_taken(client, pm, atlas, address):
    page = save(client, pm, atlas, address).content.decode()

    atlas.refresh_from_db()
    assert atlas.discord_webhook == ""
    assert "This is not a Discord webhook." in page


def test_send_test_opens_a_test_topic_in_the_forum(client, pm, atlas, fake_discord):
    save(client, pm, atlas, WEBHOOK)

    page = client.post(reverse("test_discord", args=[atlas.pk]), follow=True).content.decode()

    assert "Test topic “dimond · test” is in the forum." in page
    [body] = fake_discord.bodies()
    assert body["thread_name"] == "dimond · test"
    post = DiscordPost.objects.get()
    assert (post.kind, post.status) == (DiscordPostKind.TEST, DiscordPostStatus.SENT)


def test_send_test_says_when_the_channel_is_not_a_forum(client, pm, atlas, fake_discord):
    save(client, pm, atlas, WEBHOOK)
    fake_discord.answers = [
        httpx.Response(
            400,
            json={"message": "Webhooks can only create threads in forum channels", "code": 220003},
        )
    ]

    page = client.post(reverse("test_discord", args=[atlas.pk]), follow=True).content.decode()

    assert "Discord didn’t take the test: The channel is not a forum." in page
    assert DiscordPost.objects.get().status == DiscordPostStatus.FAILED


def test_a_failed_test_is_not_tried_again_in_the_background(client, pm, atlas, fake_discord):
    save(client, pm, atlas, WEBHOOK)
    fake_discord.answers = [httpx.ConnectError("no route")]

    client.post(reverse("test_discord", args=[atlas.pk]))

    post = DiscordPost.objects.get()
    assert (post.status, post.error) == (DiscordPostStatus.FAILED, "Discord unreachable")


def test_turning_discord_off_forgets_the_webhook(client, pm, atlas):
    save(client, pm, atlas, WEBHOOK)

    page = client.post(
        reverse("remove_discord_webhook", args=[atlas.pk]), follow=True
    ).content.decode()

    atlas.refresh_from_db()
    assert atlas.discord_webhook == ""
    assert "Discord is off for Atlas" in page


@pytest.mark.parametrize(
    "name", ["change_discord_webhook", "remove_discord_webhook", "test_discord"]
)
def test_another_pm_cannot_touch_a_clients_discord(client, other_pm, atlas, name):
    client.force_login(other_pm)

    response = client.post(reverse(name, args=[atlas.pk]), {"webhook": WEBHOOK})

    assert response.status_code == 404
    atlas.refresh_from_db()
    assert atlas.discord_webhook == ""


def test_logs_list_only_the_pms_own_clients_posts_without_the_address(client, pm, other_pm, atlas):
    atlas.discord_webhook = WEBHOOK
    atlas.save()
    theirs = Company.objects.create(name="Northwind", owner=other_pm, jira_project="NW")
    DiscordPost.objects.create(company=atlas, kind=DiscordPostKind.TASK, task_key="FI-2301")
    DiscordPost.objects.create(company=theirs, kind=DiscordPostKind.TASK, task_key="NW-14")
    client.force_login(pm)

    page = client.get(reverse("logs_discord")).content.decode()

    assert "Topic for FI-2301" in page
    assert "NW-14" not in page
    assert "abcDEF" not in page


def test_clients_cannot_open_logs_discord(client, atlas):
    anna = User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")
    client.force_login(anna)

    assert client.get(reverse("logs_discord")).status_code == 404
