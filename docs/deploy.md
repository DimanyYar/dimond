# Production: first start and deploys

How dimond runs in production (ADR 0022): one Linux server of its own, where Caddy gets the
HTTPS certificate itself. Commands run on the server as `root`.

## Preparing the server

Once, on a fresh Ubuntu 24.04 with your SSH key already added:

```
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl reload ssh
apt install -y unattended-upgrades
```

This installs Docker, lets in only SSH and the site, turns off password logins (keys only)
and installs security updates by itself. Point the domain's A record to the server's IP
before the first start: Caddy needs it to get the certificate.

## First start

1. Clone the code:

   ```
   git clone https://github.com/DimanyYar/dimond.git /srv/dimond
   cd /srv/dimond
   ```

2. Create `.env` from `.env.example` and set the production values:

   | Setting | Value |
   |---|---|
   | `DJANGO_DEBUG` | `false` |
   | `DJANGO_SECRET_KEY` | new, from `python3 -c "import secrets; print(secrets.token_urlsafe(50))"` |
   | `DJANGO_ALLOWED_HOSTS` | the domain, e.g. `dimond.example.com` |
   | `DJANGO_ADMIN_URL` | a hard-to-guess path ending in `/` |
   | `SITE_URL` | `https://` + the domain |
   | `SITE_ADDRESS` | the domain alone: Caddy gets its HTTPS certificate |
   | `HTTP_PORT`, `HTTPS_PORT` | `80` and `443` |
   | `POSTGRES_PASSWORD` | new, long |
   | `DEFAULT_FROM_EMAIL`, `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET` | from Resend (ADR 0009, 0021) |
   | `TRACKER_JIRA_*` | the Jira address, account and webhook token (ADR 0020) |

   `chmod 600 .env`: it holds every password.

3. Start: `./deploy/deploy.sh`. The first run builds the image, creates the database and
   prints `Done:` when the site answers.

4. Create the first PM. A superuser is a PM who can also open the Django admin:

   ```
   docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
   ```

5. Open the subdomain and sign in with an email link.

## Deploying

```
/srv/dimond/deploy/deploy.sh
```

The script lists the new commits and saves the database to `backups/before-deploy-….dump`.
Then it switches to the latest main, rebuilds, restarts, and opens `SITE_URL/healthz`. It
refuses to start if files in the project folder were edited on the server: what goes live is
always exactly what is on GitHub.
Run it when the client's working day is over: a restart takes the site down for a few seconds.

## Looking around

- What runs: `docker compose -f docker-compose.prod.yml ps`
- Logs of one part (`web`, `worker`, `previews`, `sync`, `caddy`, `db`):
  `docker compose -f docker-compose.prod.yml logs -f --tail 100 web`
- Which version is live: `git log -1 --oneline`

## When a deploy fails

If the new version does not come up, the script goes back to the previous commit by itself and
says so. The site then runs the old code.

If a migration of the new version already changed the database and the old code cannot work
with it, restore the dump from before the deploy. The script printed its name. The database is
dropped and created again, so nothing the new migrations added stays behind:

```
docker compose -f docker-compose.prod.yml stop web worker previews sync caddy
docker compose -f docker-compose.prod.yml exec -T db sh -c \
  'dropdb -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose -f docker-compose.prod.yml exec -T db sh -c \
  'pg_restore --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < backups/before-deploy-YYYYMMDD-HHMMSS-abc1234.dump
docker compose -f docker-compose.prod.yml up -d
```

If `pg_restore` stops with an error, do not start the site: the database is incomplete. Ask
for help with the error text.

Anything written to the site between the deploy and the restore is lost, so restore only when
the old code really cannot run.
