#!/usr/bin/env bash
# Update a release install of Shikumi to the latest (or a given) release, and roll back if the new version does not start.
#   ./scripts/update.sh              # latest release
#   ./scripts/update.sh v0.2.0       # a specific release (also how you go back)
#   ./scripts/update.sh --images-only   # used by setup.sh: fetch the images of the installed version, change nothing else
# Run it in the install folder, on the host. It refuses to run while an agent run is in flight (a restart would kill it).
# Not for a git checkout: there, `git pull` and `docker compose --profile build build`.
# Testing: DRY_RUN=1 prints the container commands instead of running them; SHIKUMI_ASSET_BASE=file:///dir serves the release files locally.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${SHIKUMI_REPO:-$(sed -n 's/^SHIKUMI_REPO=//p' .env 2>/dev/null | tail -1)}"; REPO="${REPO:-theninjadojo/software-factory}"
OWNER="$(echo "${REPO%%/*}" | tr 'A-Z' 'a-z')"
REGISTRY="ghcr.io/$OWNER"
run() { if [ -n "${DRY_RUN:-}" ]; then echo "+ $*"; else "$@"; fi; }
die() { echo "$*" >&2; exit 1; }
[ -d .git ] && [ -f Dockerfile ] && die "This is a source checkout: use git pull and docker compose --profile build build. update.sh is for release installs."
command -v docker >/dev/null || die "docker is required"
command -v curl >/dev/null || die "curl is required"
command -v python3 >/dev/null || die "python3 is required"

env_set() {  # key value: set or replace a line in .env
  touch .env
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi
}
IMAGES=("shikumi:SHIKUMI_IMAGE" "shikumi-agent:localhost/factory-agent:latest" "shikumi-render:localhost/factory-render:latest" "shikumi-screens:localhost/factory-screens:latest")

pull_images() {  # tag
  local tag="$1" entry name local_name
  for entry in "${IMAGES[@]}"; do
    name="${entry%%:*}"; local_name="${entry#*:}"
    run docker pull "$REGISTRY/$name:$tag"
    if [ "$local_name" = SHIKUMI_IMAGE ]; then env_set SHIKUMI_IMAGE "$REGISTRY/$name:$tag"
    else
      docker image inspect "$local_name" >/dev/null 2>&1 && run docker tag "$local_name" "${local_name%:*}:previous" || true
      run docker tag "$REGISTRY/$name:$tag" "$local_name"
    fi
  done
}

if [ "${1:-}" = "--images-only" ]; then
  pull_images "v$(cat VERSION)"; exit 0
fi

HAVE="$(cat VERSION 2>/dev/null || echo unknown)"
TAG="${1:-}"
if [ -z "$TAG" ]; then
  TAG="$(curl -fsS -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$REPO/releases/latest" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')" \
    || die "could not read the latest release of $REPO (offline, or none published yet?)"
fi
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "not a release tag: $TAG"
[ "v$HAVE" = "$TAG" ] && { echo "Already on $TAG."; exit 0; }
echo "Updating Shikumi: v$HAVE -> $TAG"

# never restart over a run in flight
FACTORY_HOME="$(sed -n 's/^FACTORY_HOME=//p' .env 2>/dev/null | tail -1)"; FACTORY_HOME="${FACTORY_HOME:-/srv/factory}"
if [ -z "${DRY_RUN:-}" ]; then
  N="$(docker ps -q --filter 'name=^factory-' | wc -l)"
  M="$(python3 - "$FACTORY_HOME/state/factory.db" <<'PY'
import sqlite3, sys
try:
    db = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True, timeout=5)
    print(db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0])
except Exception:
    print(0)
PY
)"
  [ "$N" = 0 ] && [ "$M" = 0 ] || die "An agent run is in flight ($N sandboxes, $M runs marked running). Try again when it finishes."
fi

BASE="${SHIKUMI_ASSET_BASE:-https://github.com/$REPO/releases/download/$TAG}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
for f in docker-compose.yml config.example.toml VERSION env.example; do      # (env.example is saved as .env.example: GitHub renames dotfile assets)
  curl -fsSL "$BASE/$f" -o "$TMP/$f" || die "could not download $f from release $TAG"
done
mv "$TMP/env.example" "$TMP/.env.example"
[ "v$(cat "$TMP/VERSION")" = "$TAG" ] || die "the release files do not match $TAG"
for f in setup.sh update.sh; do curl -fsSL "$BASE/$f" -o "$TMP/$f" || die "could not download $f"; done

# keep what a rollback needs
BK=".shikumi-backup"; rm -rf "$BK"; mkdir -p "$BK"
cp -f docker-compose.yml VERSION .env config.example.toml "$BK"/ 2>/dev/null || true

pull_images "$TAG"
cp "$TMP/docker-compose.yml" "$TMP/config.example.toml" "$TMP/VERSION" "$TMP/.env.example" .
cp "$TMP/setup.sh" scripts/setup.sh; chmod +x scripts/setup.sh
run docker compose up -d

rollback() {
  echo "The new version did not come up healthy: rolling back to v$HAVE" >&2
  [ -z "${DRY_RUN:-}" ] || return 0
  cp -f "$BK"/docker-compose.yml "$BK"/VERSION "$BK"/config.example.toml . 2>/dev/null || true
  cp -f "$BK"/.env .env 2>/dev/null || true
  for entry in "${IMAGES[@]:1}"; do l="${entry#*:}"; docker image inspect "${l%:*}:previous" >/dev/null 2>&1 && docker tag "${l%:*}:previous" "$l" || true; done
  docker compose up -d || true
  exit 1
}
if [ -z "${DRY_RUN:-}" ]; then
  PORT="$(sed -n 's/^FACTORY_UI_PORT=//p' .env | tail -1)"; PORT="${PORT:-8787}"
  ok=""; for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && { ok=1; break; }; sleep 2; done
  [ -n "$ok" ] || rollback
  sleep 5; [ -n "$(docker compose ps --status running -q orchestrator)" ] || rollback
fi
cp "$TMP/update.sh" scripts/update.sh; chmod +x scripts/update.sh      # replaced last: this script is still running
echo "Now on $TAG. Check: docker compose run --rm -e FACTORY_CONFIG=/etc/factory/config.toml orchestrator python3 -m factory.ctl doctor"
echo "Rollback later with: ./scripts/update.sh v$HAVE   (read the release notes first: a config change may be needed)"
