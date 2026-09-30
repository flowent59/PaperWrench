#!/usr/bin/env bash
# Runs only on a fresh GitHub runner. All state belongs to this unique project.
set -euo pipefail

test "${GITHUB_ACTIONS:-}" = true
test -n "${GITHUB_RUN_ID:-}" && test -n "${GITHUB_RUN_ATTEMPT:-}"
test -n "${RELEASE_VERSION:-}" && test -n "${RELEASE_DIGEST:-}"

repository=ghcr.io/flowent59/paperwrench
stack="pwrelease${GITHUB_RUN_ID}a${GITHUB_RUN_ATTEMPT}"
container="${stack}-app"
volume="${stack}_paperwrench_data"
workdir=$(mktemp -d)
chmod 700 "$workdir"
cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  docker compose -p "$stack" -f docker-compose.dev.yml down -v >/dev/null 2>&1 || true
  rm -rf -- "$workdir"
}
trap cleanup EXIT

# Pull both published tags from GHCR and prove they resolve to the same local
# platform image as the digest reported by the build. Never build this image here.
docker pull "${repository}@${RELEASE_DIGEST}"
docker pull "${repository}:${RELEASE_VERSION}"
if [ "${CHECK_LATEST:-false}" = true ]; then docker pull "${repository}:latest"; fi
mkdir "$workdir/anonymous-docker"
DOCKER_CONFIG="$workdir/anonymous-docker" docker pull "${repository}:${RELEASE_VERSION}" >/dev/null
expected_id=$(docker image inspect "${repository}@${RELEASE_DIGEST}" --format '{{.Id}}')
test "$(docker image inspect "${repository}:${RELEASE_VERSION}" --format '{{.Id}}')" = "$expected_id"
if [ "${CHECK_LATEST:-false}" = true ]; then
  test "$(docker image inspect "${repository}:latest" --format '{{.Id}}')" = "$expected_id"
fi

# The Paperless Compose file is explicitly a disposable development stack.
docker compose -p "$stack" -f docker-compose.dev.yml pull paperless
docker compose -p "$stack" -f docker-compose.dev.yml up -d db broker paperless
for attempt in $(seq 1 90); do
  if curl -fsS -o /dev/null http://127.0.0.1:8010/accounts/login/; then break; fi
  if [ "$attempt" = 90 ]; then echo 'Paperless did not start' >&2; exit 1; fi
  sleep 5
done
# The login page can answer before Paperless finishes creating the configured
# admin account. Wait for token authentication itself so a fresh stack cannot
# fail the release gate during that short readiness gap.
for attempt in $(seq 1 60); do
  if curl -fsS -X POST http://127.0.0.1:8010/api/token/ \
       -H 'Content-Type: application/json' \
       -d '{"username":"admin","password":"admin"}' 2>/dev/null |
       jq -er .token > "$workdir/paperless_token" 2>/dev/null; then
    break
  fi
  rm -f "$workdir/paperless_token"
  if [ "$attempt" = 60 ]; then echo 'Paperless admin token did not become ready' >&2; exit 1; fi
  sleep 2
done
test -s "$workdir/paperless_token"
chmod 0444 "$workdir/paperless_token"
token=$(cat "$workdir/paperless_token")

docker volume create "$volume" >/dev/null
docker run -d --name "$container" \
  --network "${stack}_default" \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  -p 127.0.0.1:18080:8000 \
  -v "${volume}:/data" \
  -e PAPERLESS_URL=http://paperless:8000 \
  "${repository}@${RELEASE_DIGEST}" >/dev/null

base=http://127.0.0.1:18080
for attempt in $(seq 1 60); do
  if curl -fsS "$base/api/v1/system/health" -o "$workdir/health.json" 2>/dev/null &&
     jq -e --arg version "$RELEASE_VERSION" '.status == "ok" and .database == "ok" and .version == $version' "$workdir/health.json" >/dev/null; then
    break
  fi
  if [ "$attempt" = 60 ]; then echo 'PaperWrench did not become healthy' >&2; exit 1; fi
  sleep 2
done

# Authenticate exactly as a v0.2 user does. The Paperless token remains in this
# private temporary directory; PaperWrench receives it only for the login call
# and returns an opaque cookie plus a non-credential CSRF token.
login() {
  rm -f "$workdir/cookies" "$workdir/session.json"
  jq -n --arg token "$token" '{token: $token, locale: "en"}' > "$workdir/login.json"
  curl -fsS -c "$workdir/cookies" -X POST "$base/api/v1/auth/login" \
    -H 'Content-Type: application/json' -H 'Origin: http://127.0.0.1:18080' \
    --data-binary @"$workdir/login.json" -o "$workdir/session.json"
  csrf=$(jq -er .csrf_token "$workdir/session.json")
}
login

# Fresh volume migration, authenticated real network connection, direct HTTP
# and SPA routes.
docker exec -i "$container" python - <<'PY'
import sqlite3

from alembic.script import ScriptDirectory
from paperwrench.db.migrate import build_alembic_config

database_url = "sqlite+pysqlite:////data/paperwrench.db"
head = ScriptDirectory.from_config(build_alembic_config(database_url)).get_current_head()
database = sqlite3.connect("/data/paperwrench.db")
assert head and database.execute("select version_num from alembic_version").fetchone() == (head,)
PY
curl -fsS -b "$workdir/cookies" "$base/api/v1/system/paperless" -o "$workdir/paperless.json"
paperless_version=$(jq -er 'select(.connected == true and .compatible == true) | .paperless_version | select(type == "string" and length > 0)' "$workdir/paperless.json")
for route in / /documents/42 /jobs/42 /history /schemas /quality; do
  curl -fsS "$base$route" -o "$workdir/page.html"
  grep -Fq '<div id="root">' "$workdir/page.html"
  cat "$workdir/page.html" >> "$workdir/responses"
done
assets=$(grep -oE '(src|href)="/assets/[^\"]+"' "$workdir/page.html" | sed -E 's/^(src|href)="([^"]+)"$/\2/')
test "$(printf '%s\n' "$assets" | wc -l)" -ge 2
for asset in $assets; do
  curl -fsS "$base$asset" -o "$workdir/asset"
  cat "$workdir/asset" >> "$workdir/responses"
done

# A database write proves the named volume survives a container restart.
curl -fsS -X POST "$base/api/v1/collections" \
  -b "$workdir/cookies" -H "X-CSRF-Token: $csrf" \
  -H 'Content-Type: application/json' -H 'Origin: http://127.0.0.1:18080' \
  -d '{"name":"GHCR release smoke","document_ids":[]}' -o "$workdir/collection.json"
jq -e '.name == "GHCR release smoke" and .id > 0' "$workdir/collection.json" >/dev/null
cat "$workdir/health.json" "$workdir/session.json" "$workdir/paperless.json" "$workdir/collection.json" >> "$workdir/responses"
docker restart "$container" >/dev/null
for attempt in $(seq 1 30); do
  if curl -fsS "$base/api/v1/system/health" -o "$workdir/health-after.json" 2>/dev/null &&
     jq -e '.status == "ok" and .database == "ok"' "$workdir/health-after.json" >/dev/null; then break; fi
  if [ "$attempt" = 30 ]; then echo 'PaperWrench did not recover after restart' >&2; exit 1; fi
  sleep 2
done
# Sessions are deliberately process-local, so a restart requires a fresh login.
login
curl -fsS -b "$workdir/cookies" "$base/api/v1/collections" -o "$workdir/collections-after.json"
jq -e 'any(.[]; .name == "GHCR release smoke")' "$workdir/collections-after.json" >/dev/null
curl -fsS -b "$workdir/cookies" "$base/api/v1/system/paperless" -o "$workdir/paperless-after.json"
jq -e '.connected == true and .compatible == true' "$workdir/paperless-after.json" >/dev/null
cat "$workdir/health-after.json" "$workdir/session.json" "$workdir/collections-after.json" "$workdir/paperless-after.json" >> "$workdir/responses"
docker logs "$container" > "$workdir/app-logs" 2>&1
if grep -Fq -- "$token" "$workdir/responses" "$workdir/app-logs"; then
  echo 'Paperless token appeared in an HTTP response or application log' >&2
  exit 1
fi
echo "Verified remote ${repository}@${RELEASE_DIGEST}: Paperless ${paperless_version} (latest), migration, direct HTTP, SPA, persistence and token redaction"
