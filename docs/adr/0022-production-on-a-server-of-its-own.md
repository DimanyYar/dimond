# 0022. Production on a server of its own, deployed with one command

Status: Accepted, 2026-10-22

## Context

dimond goes live for the pilot with Atlas (#21). The first plan was a place on Shakuro's shared
experiments server, behind its HTTPS proxy. On Oct 23, 2026 Softlight gave dimond a server of
its own instead (Hetzner CX23: 2 vCPU, 4 GB RAM, 40 GB disk), with root access. Jira
(`j.shakuro.com`) is reachable from the internet. One PM ships a few times a week; clients
work while it is night in Moscow.

## Decision

- `docker-compose.prod.yml` runs the same image as development: Postgres, web on gunicorn,
  the email, preview and Jira sync processes (ADR 0004, 0013, 0019), and Caddy.
- Caddy is the front. With `SITE_ADDRESS` set to the domain it gets and renews the HTTPS
  certificate itself (Let's Encrypt) and redirects HTTP to HTTPS. With `SITE_ADDRESS=:80` it
  serves plain HTTP for a proxy that does HTTPS, and trusts that proxy's
  `X-Forwarded-Proto: https` (private addresses only). Either way it serves CSS and scripts
  itself and passes everything else to Django. Requests over 128 MB are refused there: the
  largest real one is 5 videos of 20 MB each.
- web runs the migrations and collects static files each time it starts, so the database is
  never behind the code. The background processes start only once web answers its health
  check, so none of them runs against a database whose migrations have not run.
- Gunicorn: 3 processes with 4 threads each and a 300-second timeout, so a long video upload
  or download does not hold up other people.
- Deploying is `deploy/deploy.sh` on the server, started by hand when the PM chooses: a
  merge into main changes nothing in production. The script dumps the database, switches the
  code to `origin/main`, rebuilds, restarts, and checks `/healthz` through Caddy. If the new
  version does not come up, it goes back to the previous commit. It refuses to run when files
  in the project folder were edited on the server.
- Automatic deploys from GitHub Actions were rejected for now: GitHub would have to hold an SSH
  key to a company server, and an update in the middle of the client's working day is worse
  than one the PM times.

## Consequences

+ The server needs only Docker, a firewall with SSH, 80 and 443 open, and a domain pointing to
  it; certificates renew by themselves.
+ Every deploy leaves a database dump next to the code (the last 10 are kept).
− Going back to the previous code does not undo a migration that already ran. Most of
  dimond's migrations only add things, so the old code still works; when it does not, the
  dump from before the deploy is restored by hand (docs/deploy.md).
− Files are still streamed by Django (apps/files), not by Caddy as the architecture plans;
  that is a separate change.
