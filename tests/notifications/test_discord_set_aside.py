"""A Discord post dimond cannot build fails on its own and the posts behind it still go out
(PRD: Discord: details; ADR 0024). Discord is the fake_discord stand-in from
tests/conftest.py."""

import pytest

from apps.accounts.models import Company, User
from apps.notifications import services as notifications
from apps.notifications.models import DiscordPost, DiscordPostKind, DiscordPostStatus
from apps.notifications.services import NOT_BUILT_POST, NOT_SURE_POST, send_discord_posts
from apps.requests import services
from apps.requests.models import Message, Request, RequestType, WhenNeeded

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


def planned(pm: User, author: User, title: str) -> Request:
    request = services.create_request(
        author, RequestType.BUG, title, why="", when_needed=WhenNeeded.HOTFIX
    )
    return services.create_jira_task(
        pm, request, issue_type="Bug", summary=title, description="", reply=""
    )


@pytest.fixture
def broken_reply_text(monkeypatch):
    """A bug in how a reply reads in the topic: every reply post breaks while being built."""
    words = notifications._client_words

    def words_or_break(headline: str, source: Message | object) -> str:
        if isinstance(source, Message):
            raise KeyError("author")
        return words(headline, source)

    monkeypatch.setattr(notifications, "_client_words", words_or_break)


def test_a_post_that_cannot_be_built_fails_with_the_reason_in_logs(
    pm, anna, fake_discord, broken_reply_text
):
    request = planned(pm, anna, "Duplicate leads")
    send_discord_posts()
    services.post_message(anna, request, "It happens on Safari only.")

    assert send_discord_posts() == 0

    post = DiscordPost.objects.get(kind=DiscordPostKind.REPLY)
    assert post.status == DiscordPostStatus.FAILED
    assert post.error == NOT_BUILT_POST
    assert post.error_detail == "KeyError: 'author'"
    assert post.attempts == 1


def test_posts_behind_a_broken_one_still_go_out(pm, anna, fake_discord, broken_reply_text):
    first = planned(pm, anna, "Duplicate leads")
    send_discord_posts()
    services.post_message(anna, first, "It happens on Safari only.")
    second = planned(pm, anna, "Export times out")

    assert send_discord_posts() == 1

    task = DiscordPost.objects.get(request=second, kind=DiscordPostKind.TASK)
    assert task.status == DiscordPostStatus.SENT


def test_a_broken_post_is_not_tried_again(pm, anna, fake_discord, broken_reply_text):
    request = planned(pm, anna, "Duplicate leads")
    send_discord_posts()
    services.post_message(anna, request, "It happens on Safari only.")
    send_discord_posts()
    fake_discord.posts.clear()

    send_discord_posts()

    assert fake_discord.posts == []
    assert DiscordPost.objects.get(kind=DiscordPostKind.REPLY).attempts == 1


def test_a_later_post_about_the_same_request_is_not_held_back(pm, anna, fake_discord, monkeypatch):
    request = planned(pm, anna, "Duplicate leads")
    send_discord_posts()
    services.post_message(anna, request, "It happens on Safari only.")
    words = notifications._client_words
    monkeypatch.setattr(notifications, "_client_words", lambda *args: 1 / 0)
    send_discord_posts()
    monkeypatch.setattr(notifications, "_client_words", words)
    services.post_message(anna, request, "And on Firefox.")

    assert send_discord_posts() == 1
    [body] = fake_discord.bodies()[-1:]
    assert "And on Firefox." in body["content"]


def test_a_post_that_broke_after_discord_took_it_is_not_called_unbuilt_or_sent_again(
    pm, anna, fake_discord, monkeypatch
):
    request = planned(pm, anna, "Duplicate leads")
    send_discord_posts()
    services.post_message(anna, request, "It happens on Safari only.")
    post_in_topic = notifications.discord.post_in_topic

    def post_then_break(webhook: str, topic: str, text: str) -> str:
        post_in_topic(webhook, topic, text)
        raise RuntimeError("database gone")

    monkeypatch.setattr(notifications.discord, "post_in_topic", post_then_break)
    fake_discord.posts.clear()

    send_discord_posts()
    send_discord_posts()

    assert len(fake_discord.posts) == 1
    post = DiscordPost.objects.get(kind=DiscordPostKind.REPLY)
    assert post.status == DiscordPostStatus.FAILED
    assert post.error == NOT_SURE_POST
    assert post.attempts == 1
