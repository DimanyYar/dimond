# dimond

A client request portal on top of Jira. Clients send bugs, improvements, features and ideas
with the context the team actually needs, and follow progress in plain words. The PM triages
everything in one inbox, turns requests into Jira tasks, and statuses flow back automatically.

> Status: foundation. Features land in small pull requests; see `docs/adr/` for why things are
> built the way they are.

## Stack

Django 5.2 · PostgreSQL · HTMX · plain CSS with design tokens · Docker Compose · uv · ruff ·
mypy · pytest · GitHub Actions

## Run it locally

You need Docker (OrbStack or Docker Desktop), [uv](https://docs.astral.sh/uv/) and git.

```bash
git clone https://github.com/DimanyYar/dimond.git
cd dimond
cp .env.example .env
docker compose up --build
```

- App: http://localhost:8000/healthz should answer `{"status": "ok"}`
- Mail catcher (Mailpit): http://localhost:8025, where every email the app sends lands locally
- Sign in: open http://localhost:8000/, enter the email of a person added in the admin, then open
  the link from Mailpit. The `worker` container sends emails every 5 seconds; its log is
  `docker compose logs -f worker`

Attached files land in `uploads/` in the project folder (ignored by git; `FILES_ROOT` moves
it). No address serves that folder: a file opens only through `/files/<id>/` after an access
check.

The `previews` container draws the small pictures on file tiles a few seconds after upload
(ADR 0013); its log is `docker compose logs -f previews`. Without it, tiles say "preview soon".

Stop with `Ctrl+C`; `docker compose down` removes the containers (data stays in a volume).

### Create your admin account (once)

```bash
docker compose exec web python manage.py createsuperuser
```

Then open http://localhost:8000/admin-local/ (the address comes from `DJANGO_ADMIN_URL` in
`.env`) and sign in with that email and password. This is the only account with a password.

## Run the checks

The database must be running (`docker compose up db` in another terminal is enough).

```bash
uv sync
export UV_ENV_FILE=.env   # once per terminal: lets uv read the settings from .env
uv run ruff format . && uv run ruff check .
uv run mypy
uv run pytest
```

## How we work

- `CONTRIBUTING.md`: branches, pull requests, review, releases
- `CLAUDE.md`: code rules for people and AI assistants
- `docs/adr/`: architecture decision records
