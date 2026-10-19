# dimond — instructions for Claude

dimond is a client request portal on top of Jira Server. Clients submit bugs, improvements,
features and ideas and follow their status in plain words; the PM triages them in one inbox
and creates or links Jira tasks. Product rules live in the PRD; decisions live in `docs/adr/`.
Read the ADRs before changing anything they cover.

Dmitry (the owner) is a PM learning the codebase. Every PR must be explainable to him in plain
words: what changed, why, and how to check it by hand.

## Layout

- `config/` — settings (env vars only), urls, wsgi.
- `apps/<area>/` — one Django app per PRD area: `accounts`, `requests`, `tracker`,
  `notifications`, `files`, `workspace`, `core`. Create an app only when its first real
  feature lands.
- `tests/` mirrors `apps/`. `docs/adr/` holds numbered decision records.

## Layers inside an app (ADR 0005)

- `models.py` — data and rules about the data itself.
- `services.py` — every action (create request, link to Jira, revoke access). Business logic
  and transactions live here and nowhere else.
- `selectors.py` — every read. Each selector takes the acting user and scopes the query to
  what that user may see. Views never call `Model.objects` directly.
- `views.py` — parse input, call one service or selector, render. No logic.

## Code rules

- Python 3.13, Django 5.2 LTS. English names that match the PRD (`Request`, `Company`,
  `Membership`, `StatusEvent`, `ClientStatus`).
- Type hints on every public function. `services` and `apps.tracker` must pass `mypy --strict`.
- Store time in UTC; convert with `zoneinfo` at the edges.
- Settings come from env vars; add every new one to `.env.example`.
- Frontend: Django templates + HTMX + plain CSS with the UI-kit tokens as CSS variables. No
  frontend build step (ADR 0006).
- Tests use pytest. Never call the real Jira or any network in tests: use the mock adapter
  and recorded JSON responses.

## Anti-slop rules

1. No abstractions for imagined futures. A base class or factory appears with the second real
   case, not the first.
2. Comments explain *why*, never *what*. If code needs a "what" comment, rename things instead.
3. Catch only errors you can handle meaningfully. No `except Exception: pass`, no defensive
   `None` checks for values that cannot be `None`.
4. One home for each thing: permissions in selectors, statuses in one enum, copy-paste a second
   time means it needs a function.
5. A new dependency needs an ADR: what it gives, why Django is not enough, what if it dies.
6. Tests check behaviour named after the PRD
   (`test_client_cannot_open_other_company_request`), never that code exists.
7. One PR does one thing. Drive-by refactors go into their own PR. Keep it under ~800 changed
   lines; a bigger feature is cut into steps.
8. No dead code: no commented-out blocks, no `TODO: implement later`, no unused parameters.
9. If you cannot explain a line to Dmitry in one sentence, it does not belong in the PR.

## Before opening a PR

Run with `UV_ENV_FILE=.env` set and make green:

```
uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest
uv run python manage.py makemigrations --check --dry-run
```

Fill in the PR template. UI changes need a screenshot. If behaviour changed, say which PRD
section needs an update.
