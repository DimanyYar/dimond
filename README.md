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

Stop with `Ctrl+C`; `docker compose down` removes the containers (data stays in a volume).

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
