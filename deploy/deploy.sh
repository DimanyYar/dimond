#!/usr/bin/env bash
# Updates production to the latest main (ADR 0022). Run on the server, from any folder:
#
#   /srv/dimond/deploy/deploy.sh
#
# It backs up the database, switches the code to origin/main, rebuilds and restarts, and checks
# the site answers. If the new version does not come up, it goes back to the previous one.
set -euo pipefail

cd "$(dirname "$0")/.."

compose() { docker compose -f docker-compose.prod.yml "$@"; }

# The address Caddy listens on and the site's own host name, both from .env.
setting() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- | tr -d '"'"'" || true; }
http_port=$(setting HTTP_PORT)
http_port=${http_port:-127.0.0.1:8080}
site_host=$(setting SITE_URL | sed -E 's#^https?://##; s#/.*$##')

site_answers() {
	curl --fail --silent --show-error --max-time 10 \
		--header "Host: $site_host" "http://$http_port/healthz" >/dev/null
}

start() {
	compose build --quiet
	compose up --detach --remove-orphans --wait --wait-timeout 300 && site_answers
}

previous=$(git rev-parse HEAD)
git fetch --quiet origin main
next=$(git rev-parse origin/main)

if [ "$previous" = "$next" ] && compose ps --status running --quiet web | grep -q .; then
	echo "Already on $(git log -1 --format='%h %s' "$next"). Nothing to deploy."
	exit 0
fi

echo "Deploying:"
git log --oneline "$previous..$next" || true

# A copy of the database from before this deploy, in case a migration has to be undone.
# The last 10 stay; daily backups away from the server are separate (#21).
mkdir -p backups
if compose ps --status running --quiet db | grep -q .; then
	dump="backups/before-deploy-$(date -u +%Y%m%d-%H%M%S)-$(git rev-parse --short "$previous").dump"
	compose exec -T db sh -c 'pg_dump --format=custom -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >"$dump"
	echo "Database saved to $dump"
	ls -1t backups/before-deploy-*.dump | tail -n +11 | xargs -r rm --
fi

git checkout --quiet --detach "$next"
if start; then
	docker image prune --force >/dev/null
	echo "Done: $(git log -1 --format='%h %s')"
	exit 0
fi

echo "The new version did not come up. Going back to $(git rev-parse --short "$previous")." >&2
compose logs --tail 50 web >&2 || true
git checkout --quiet --detach "$previous"
if start; then
	echo "Back on the previous version. If the new one changed the database, see docs/deploy.md." >&2
else
	echo "The previous version did not come up either. See docs/deploy.md, \"When a deploy fails\"." >&2
fi
exit 1
