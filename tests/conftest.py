import json

import httpx
import pytest

from apps.accounts.models import Role, User


@pytest.fixture
def pm(db) -> User:
    """The PM who owns the test clients (PRD: every PM has their own clients)."""
    return User.objects.create_user("dmitry@shakuro.com", name="Dmitry", role=Role.PM)


@pytest.fixture(autouse=True)
def private_folder(settings, tmp_path):
    """Every test writes files to its own temporary folder, never to uploads/: the sync and
    the answers to the team store files too, not only the files tests."""
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture(autouse=True)
def no_real_discord(monkeypatch):
    """No test ever reaches Discord: a test that posts without its own stand-in fails here."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("A test tried to reach Discord; use the fake_discord fixture.")

    monkeypatch.setattr("apps.notifications.discord.transport", httpx.MockTransport(refuse))


class FakeDiscord:
    """Discord's webhook endpoint as tests need it: remembers every post and answers with the
    queued answers, then with a new topic. An exception in the queue is raised as the network
    failing."""

    TOPIC = "1300000000000000777"

    def __init__(self) -> None:
        self.posts: list[httpx.Request] = []
        self.answers: list[httpx.Response | Exception] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.posts.append(request)
        if not self.answers:
            return httpx.Response(200, json={"id": "1", "channel_id": self.TOPIC})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def bodies(self) -> list[dict]:
        return [json.loads(post.content) for post in self.posts]


@pytest.fixture
def fake_discord(monkeypatch) -> FakeDiscord:
    fake = FakeDiscord()
    monkeypatch.setattr("apps.notifications.discord.transport", httpx.MockTransport(fake.handle))
    return fake
