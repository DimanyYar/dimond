"""Posts to a client's Discord forum channel through its webhook (PRD: notifications,
"Discord for the team"; ADR 0024).

A webhook address is the only key: whoever has it can post into that one channel. It never
leaves this module except to Discord, never lands in a log, and the PM sees only its last
characters. Only Discord's own hosts are accepted, so a pasted address can never make dimond
call some other server.
"""

import math
import re

import httpx

TIMEOUT_SECONDS = 10
MAX_WAIT_SECONDS = 3600.0
WEBHOOK = re.compile(r"^https://discord\.com/api/webhooks/\d+/[\w-]+$")
# Discord's own limits: a topic name of 100 characters, a message of 2000.
TOPIC_NAME_LIMIT = 100
MESSAGE_LIMIT = 2000

# Discord's error codes that need words of their own.
UNKNOWN_WEBHOOK = 10015
NOT_A_FORUM = (220001, 220003)

# Tests put an httpx.MockTransport here (tests/conftest.py); None is the real network.
transport: httpx.BaseTransport | None = None


class DiscordError(Exception):
    """Discord did not take the post. `reason` is for the PM's eyes, `detail` is Discord's
    own words. `retry_after` is set, in seconds, when trying again later can help."""

    def __init__(self, reason: str, detail: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.retry_after = retry_after


def is_webhook(address: str) -> bool:
    return bool(WEBHOOK.match(address))


def webhook_tail(address: str) -> str:
    """What the PM sees of a saved webhook: enough to tell two apart, too little to use."""
    return f"…/{address[-4:]}"


def fit(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def start_topic(webhook: str, name: str, text: str) -> str:
    """A new topic in the forum with its first post; returns the topic's id."""
    return _post(webhook, {"thread_name": fit(name, TOPIC_NAME_LIMIT), "content": text})


def _post(webhook: str, body: dict[str, object]) -> str:
    # A client's words may hold @everyone or a role mention: nobody gets pinged by dimond.
    body = {**body, "allowed_mentions": {"parse": []}}
    try:
        with httpx.Client(
            timeout=TIMEOUT_SECONDS, follow_redirects=False, transport=transport
        ) as client:
            response = client.post(webhook, params={"wait": "true"}, json=body)
    except httpx.TransportError as error:
        raise DiscordError(
            "Discord unreachable", str(error) or type(error).__name__, retry_after=0
        ) from None
    if not response.is_success:
        raise _refusal(response)
    # With wait=true Discord returns the message; in a new topic its channel is the topic.
    try:
        topic = response.json()["channel_id"]
    # Discord took the post but its answer is not the message, e.g. a proxy's page. Trying
    # again would open a second topic, so the post is not retried.
    except (ValueError, KeyError, TypeError):
        topic = None
    # Discord's ids are numbers written as strings ("snowflakes"); isdecimal() alone would
    # also take digits of other scripts.
    if isinstance(topic, str) and topic.isascii() and topic.isdecimal():
        return topic
    # Only the status: the answer holds the posted text, which the journal never keeps.
    raise DiscordError(
        "Posted, but Discord's answer was unreadable", f"{response.status_code} without a topic id"
    )


def _refusal(response: httpx.Response) -> DiscordError:
    try:
        answer = response.json()
    # An error page from a proxy or Discord's edge comes without JSON.
    except ValueError:
        answer = {}
    if not isinstance(answer, dict):
        answer = {}
    detail = f"{response.status_code} {answer.get('message') or response.reason_phrase}"
    code = answer.get("code")
    if response.status_code == 429:
        return DiscordError("Too many posts at once", detail, retry_after=_seconds(answer))
    if response.status_code >= 500:
        return DiscordError("Discord is having trouble", detail, retry_after=0)
    if code == UNKNOWN_WEBHOOK or response.status_code in (401, 403, 404):
        return DiscordError("The webhook was deleted in Discord", detail)
    if code in NOT_A_FORUM:
        return DiscordError("The channel is not a forum", detail)
    return DiscordError("Discord refused the post", detail)


def _seconds(answer: dict[str, object]) -> float:
    """How long Discord asks to wait, up to an hour; 5 seconds when the answer does not say it
    clearly. Discord asks for seconds; anything longer than the hourly retries is not real."""
    try:
        seconds = float(str(answer.get("retry_after", 5)))
    except ValueError:
        return 5.0
    return min(max(seconds, 0.0), MAX_WAIT_SECONDS) if math.isfinite(seconds) else 5.0
