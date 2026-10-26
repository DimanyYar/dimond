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

# The site's public address from .env: checking it goes the whole way a client's request goes.
site_url=$(grep -E '^SITE_URL=' .env | tail -n 1 | cut -d= -f2- | tr -d '"'"'")

site_answers() {
	curl --fail --silent --show-error --max-time 10 "$site_url/healthz" >/dev/null
}

start() {
	compose build --quiet || return
	compose up --detach --remove-orphans --wait --wait-timeout 300 && site_answers
}

previous=$(git rev-parse HEAD)
git fetch --quiet origin main
next=$(git rev-parse origin/main)

if [ "$previous" = "$next" ] && site_answers 2>/dev/null; then
	echo "Already on $(git log -1 --format='%h %s' "$next"). Nothing to deploy."
	exit 0
fi

echo "Deploying:"
git log --oneline "$previous..$next" || true

# The image is built from this folder, so a local edit would go live unnoticed.
if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
	echo "The project folder has changes that are not on GitHub:" >&2
	git status --short >&2
	exit 1
fi

# A copy of the database from before this deploy, in case a migration has to be undone. The
# database starts first if it was stopped, so the copy is always made before new migrations.
# The last 10 stay; daily backups away from the server are separate (#21).
mkdir -p backups
compose up --detach --wait db
dump="backups/before-deploy-$(date -u +%Y%m%d-%H%M%S)-$(git rev-parse --short "$previous").dump"
compose exec -T db sh -c 'pg_dump --format=custom -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >"$dump"
echo "Database saved to $dump"
ls -1t backups/before-deploy-*.dump | tail -n +11 | xargs -r rm --

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
