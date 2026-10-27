# 0024. Discord topics for the team through each client's webhook

Status: Accepted, 2026-10-27

## Context

The team works in Discord, one server per project (client), and has no access to dimond's
pages. When the PM creates or links a Jira task, the team should see it in that client's
Discord, and later hear the client's replies about it, without a busy shared chat (PRD:
notifications, "Discord for the team").

Discord offers bots and webhooks. A bot needs an application, a token with access to every
server it joins and a running connection; a webhook is one address per channel that only
accepts posts into that channel. A webhook can open a topic in a forum channel
(`thread_name`), write into an existing topic (`thread_id`) and, with `wait=true`, return the
message, whose `channel_id` is the topic's id.

## Decision

- **Webhooks, one per client, into a forum channel.** Each Jira task gets its own topic
  "FI-2301 · title" with the type, the urgency, "Why it matters" and the Jira link. The link
  goes to Jira because the team cannot open dimond. Only forum channels: a text channel cannot
  hold a topic per task, so Send test says "The channel is not a forum".
- **The address is kept in the database on the client**, set by the PM on the Clients page.
  Unlike the Jira password (ADR 0017), which reaches every client's tasks, a webhook only
  lets someone post into one channel and is revoked by deleting it in Discord. So it is not
  encrypted; it is never shown back (only "…/a1b2"), never put in the admin, logs or the
  journal. Only `https://discord.com/api/webhooks/…` addresses are accepted, the form
  Discord's Copy Webhook URL gives, so a pasted address can never make dimond call another
  server.
- **A queue in the database and a `discord` worker** (ADR 0004), apart from the email worker
  so Discord being down never delays a sign-in link. Linking the task and queuing its post
  happen in one transaction; a Discord failure never stops the task. Failures that may pass
  (Discord unreachable, 5xx, 429) are tried after 1, 2, 5, 15 and 30 minutes, then hourly,
  for a day, respecting Discord's `retry_after`. A deleted webhook or a channel that is not a
  forum is not tried again.
- **Every post is a row in Logs · Discord**: client, task, status, tries and why it failed.
  The text is built from the request when it goes out and is not stored.
- **httpx**, already in the project (ADR 0017), sends the posts; tests use `MockTransport`,
  and a test that forgets to set one fails instead of reaching Discord.
- **Nobody is pinged**: every post sends `allowed_mentions: {parse: []}`, since a client's
  words may hold `@everyone`.

## Consequences

+ No new dependency, no bot to host; a PM sets up a client in a minute.
+ A Discord outage costs a delay, not a lost post, for a day.
− Anyone who sees the webhook address can post into that channel until it is deleted in
  Discord; a database backup holds the addresses in plain text.
− Discord offers forum channels only on servers with Community turned on (Server settings →
  Enable Community). It does not make a server public, but it changes a few defaults, such
  as verified emails for posting; turned on for the first client on Oct 27, 2026.
− Topics are not closed or renamed by dimond: a webhook cannot do it. The team archives
  finished topics by hand; status changes stay in Jira.
