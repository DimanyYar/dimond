# 0022. Production on a Shakuro server, deployed with one command

Status: Accepted, 2026-10-22

## Context

dimond goes live for the pilot with Atlas (#21). Shakuro gives it a place on its experiments
server and a subdomain of `experiments.shakuro.info`. Shakuro's own proxy on that server holds
the HTTPS certificate and passes requests on. Jira (`j.shakuro.com`) is reachable from the
internet. One PM ships a few times a week; clients work while it is night in Moscow.

## Decision

- `docker-compose.prod.yml` runs the same image as development: Postgres, web on gunicorn,
  the email, preview and Jira sync processes (ADR 0004, 0013, 0019), and Caddy.
- Caddy listens on HTTP only, on `HTTP_PORT` (`127.0.0.1:8080` by default), for Shakuro's
  proxy. It trusts that proxy's `X-Forwarded-Proto: https` (requests from private addresses
  only), serves CSS and scripts itself, and passes everything else to Django. HTTPS, the
  certificate and its renewal stay with Shakuro.
- web runs the migrations and collects static files each time it starts, so the database is
  never behind the code. The background processes start only once web answers its health
  check, so none of them runs against a database whose migrations have not run.
- Gunicorn: 3 processes with 4 threads each and a 300-second timeout, so a long video upload
  or download does not hold up other people.
- Deploying is `deploy/deploy.sh` on the server, started by hand when the PM chooses: a
  merge into main changes nothing in production. The script dumps the database, switches the
  code to `origin/main`, rebuilds, restarts, and checks `/healthz` through Caddy. If the new
  version does not come up, it goes back to the previous commit.
- Automatic deploys from GitHub Actions were rejected for now: GitHub would have to hold an SSH
  key to a company server, and an update in the middle of the client's working day is worse
  than one the PM times.

## Consequences

+ The server needs only Docker and a folder; no certificates or ports 80/443 to manage.
+ Every deploy leaves a database dump next to the code (the last 10 are kept).
− Going back to the previous code does not undo a migration that already ran. Most of
  dimond's migrations only add things, so the old code still works; when it does not, the
  dump from before the deploy is restored by hand (docs/deploy.md).
− Anyone with a shell on the same server can reach Caddy's port and claim HTTPS. The port is
  bound to 127.0.0.1, and on a shared server that is accepted for the pilot.
− Files are still streamed by Django (apps/files), not by Caddy as the architecture plans;
  that is a separate change.
