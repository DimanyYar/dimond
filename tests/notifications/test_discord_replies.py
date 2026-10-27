"""Client replies, asks to withdraw and the PM's decision in the task's Discord topic (PRD:
Discord: details; ADR 0024). Discord is the fake_discord stand-in from tests/conftest.py."""

from datetime import timedelta

import httpx
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import Company, User
from apps.files.uploads import check_upload
from apps.notifications.models import DiscordPost, DiscordPostKind, DiscordPostStatus
from apps.notifications.services import send_discord_posts
from apps.requests import services
from apps.requests.models import ClientStatus, Message, Request, RequestType, WhenNeeded

pytestmark = pytest.mark.django_db

WEBHOOK = "https://discord.com/api/webhooks/1290000000000000001/abcDEF-123_xyz"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(
        name="Atlas", owner=pm, jira_project="FI", discord_webhook=WEBHOOK
    )


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas.test", company=atlas, name="Anna K.")


def make(author: User) -> Request:
    return services.create_request(
        author,
        RequestType.BUG,
        "Duplicate leads from the web form",
        why="Managers call the same person twice.",
        description="Every submission creates two leads.",
        link="",
        when_needed=WhenNeeded.HOTFIX,
    )


@pytest.fixture
def planned(pm, anna, fake_discord) -> Request:
    """A request with a task whose topic is already in the forum."""
    request = services.create_jira_task(
        pm, make(anna), issue_type="Bug", summary="Duplicate leads", description="", reply=""
    )
    send_discord_posts()
    fake_discord.posts.clear()
    request.refresh_from_db()
    return request


def posted(fake_discord) -> list[str]:
    return [body["content"] for body in fake_discord.bodies()]


def test_a_clients_reply_goes_into_the_tasks_topic(planned, anna, fake_discord):
    services.post_message(anna, planned, "It happens on Safari only.")

    assert send_discord_posts() == 1

    [post] = fake_discord.posts
    assert post.url.params["thread_id"] == planned.discord_topic
    [body] = fake_discord.bodies()
    assert "thread_name" not in body
    assert body["content"] == "**Anna K. replied**\nIt happens on Safari only."
    assert body["allowed_mentions"] == {"parse": []}
    assert DiscordPost.objects.get(kind=DiscordPostKind.REPLY).status == DiscordPostStatus.SENT


def test_a_reply_tells_how_many_files_came_with_it_without_links(planned, anna, fake_discord):
    picture = check_upload(SimpleUploadedFile("leads.png", PNG))
    services.post_message(anna, planned, "See the screenshot.", files=[picture])
    send_discord_posts()

    [text] = posted(fake_discord)
    assert text.endswith("\n+ 1 file in dimond")
    assert "http" not in text


def test_a_long_reply_is_cut_with_an_ellipsis(planned, anna, fake_discord):
    services.post_message(anna, planned, "Leads " * 400)
    send_discord_posts()

    [text] = posted(fake_discord)
    reply = text.split("\n", 1)[1]
    assert len(reply) <= 1500
    assert reply.endswith("…")


def test_the_pms_own_replies_stay_out_of_discord(planned, pm, fake_discord):
    services.post_message(pm, planned, "Thanks, passing it on.")

    assert send_discord_posts() == 0
    assert not DiscordPost.objects.filter(kind=DiscordPostKind.REPLY).exists()


def test_replies_before_there_is_a_task_stay_out_of_discord(pm, anna, fake_discord):
    services.post_message(anna, make(anna), "One more detail.")

    assert send_discord_posts() == 0
    assert not DiscordPost.objects.exists()


def test_the_clients_answer_to_the_team_goes_into_the_topic(planned, anna, fake_discord):
    Message.objects.create(
        request=planned, text="Which browser?", team_comment_id="10001", team_author="Igor"
    )
    services.change_status(planned, ClientStatus.NEEDS_INPUT, None)

    services.answer_team(anna, planned, "Safari 17.")
    send_discord_posts()

    assert posted(fake_discord) == ["**Anna K. replied**\nSafari 17."]


def test_a_task_linked_before_discord_gets_its_topic_with_the_first_reply(
    planned, anna, fake_discord
):
    Request.objects.filter(pk=planned.pk).update(discord_topic="")

    services.post_message(anna, planned, "Still happening.")
    send_discord_posts()

    opening, reply = fake_discord.bodies()
    assert opening["thread_name"] == f"{planned.jira_key} · {planned.title}"
    assert f"Jira: {planned.jira_url}" in opening["content"]
    assert reply["content"] == "**Anna K. replied**\nStill happening."
    planned.refresh_from_db()
    assert planned.discord_topic == fake_discord.TOPIC


def test_two_replies_in_one_round_share_one_new_topic(planned, anna, fake_discord):
    Request.objects.filter(pk=planned.pk).update(discord_topic="")
    services.post_message(anna, planned, "First.")
    services.post_message(anna, planned, "Second.")

    assert send_discord_posts() == 2

    assert [("thread_name" in body) for body in fake_discord.bodies()] == [True, False, False]


def test_a_topic_deleted_in_discord_is_opened_again(planned, anna, fake_discord):
    fake_discord.answers = [httpx.Response(404, json={"message": "Unknown Channel", "code": 10003})]

    services.post_message(anna, planned, "Any news?")
    assert send_discord_posts() == 1

    gone, opening, reply = fake_discord.bodies()
    assert gone["content"] == reply["content"] == "**Anna K. replied**\nAny news?"
    assert opening["thread_name"].startswith(planned.jira_key)


def test_a_reply_waits_for_the_topic_still_on_its_way(pm, anna, fake_discord):
    fake_discord.answers = [httpx.ConnectError("no route")]
    request = services.create_jira_task(
        pm, make(anna), issue_type="Bug", summary="Duplicate leads", description="", reply=""
    )
    services.post_message(anna, request, "Still happening.")
    start = timezone.now()

    assert send_discord_posts(start) == 0
    reply = DiscordPost.objects.get(kind=DiscordPostKind.REPLY)
    assert (reply.status, reply.attempts) == (DiscordPostStatus.WAITING, 0)

    assert send_discord_posts(start + timedelta(minutes=1)) == 2
    assert [("thread_name" in body) for body in fake_discord.bodies()[1:]] == [True, False]


def test_an_ask_to_withdraw_is_one_line_with_the_reason(planned, anna, fake_discord):
    services.withdraw_request(anna, planned, "We dropped the web form.")

    assert send_discord_posts() == 1

    assert posted(fake_discord) == [
        "**Anna K. asks to withdraw the request.** The PM will decide whether work on it "
        "stops.\nWe dropped the web form."
    ]


def test_the_pms_decision_to_withdraw_follows_in_the_topic(planned, anna, pm, fake_discord):
    services.withdraw_request(anna, planned, "We dropped the web form.")
    services.decide_withdrawal(pm, planned, confirm=True)

    assert send_discord_posts() == 2

    assert posted(fake_discord)[1] == (
        f"**{pm.name or pm.email} withdrew the request.** Work on it stops; "
        "the Jira task stays as it is."
    )


def test_the_pms_decision_to_keep_it_going_follows_in_the_topic(planned, anna, pm, fake_discord):
    services.withdraw_request(anna, planned, "We dropped the web form.")
    services.decide_withdrawal(pm, planned, confirm=False)
    send_discord_posts()

    assert posted(fake_discord)[1] == (
        f"**{pm.name or pm.email} kept the request going.** Work on it goes on."
    )


def test_a_reply_after_the_task_was_deleted_in_jira_is_not_posted(planned, anna, fake_discord):
    services.post_message(anna, planned, "Any news?")
    services.drop_deleted_task(planned)

    send_discord_posts()

    post = DiscordPost.objects.get(kind=DiscordPostKind.REPLY)
    assert (post.status, post.error) == (
        DiscordPostStatus.FAILED,
        "The request no longer has a Jira task",
    )
    assert not fake_discord.posts


def test_a_reply_that_opened_the_new_tasks_topic_leaves_no_second_topic(
    planned, anna, pm, fake_discord
):
    fake_discord.answers = [httpx.ConnectError("no route")]
    services.post_message(anna, planned, "Any news?")
    send_discord_posts()
    services.drop_deleted_task(planned)
    services.link_jira_task(pm, planned, "FI-2231", "")

    send_discord_posts(timezone.now() + timedelta(minutes=2))

    names = [body["thread_name"] for body in fake_discord.bodies() if "thread_name" in body]
    assert len(names) == 1
    assert names[0].startswith("FI-2231 · ")
    task_post = DiscordPost.objects.get(kind=DiscordPostKind.TASK, task_key="FI-2231")
    assert task_post.status == DiscordPostStatus.SENT
