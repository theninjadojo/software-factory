#!/usr/bin/env bash
# Update a NATIVE install (rootless Podman + systemd, /srv/factory) to a GitHub release, and roll back if it does not come up.
# Run as the factory user on the host:
#   sudo -n -u factory /srv/factory/app/deploy/update-native.sh            # the latest release
#   sudo -n -u factory /srv/factory/app/deploy/update-native.sh v0.2.1     # a specific one (also how you go back)
#   update-native.sh --auto          # for the optional timer (deploy/systemd/shikumi-update.timer): applies a newer PATCH release only,
#                                    # and quietly does nothing while a run is in flight or when the release is a new minor/major version
# It reads the release's source tarball from GitHub (a private repo needs a token: SHIKUMI_TOKEN_FILE, default
# /srv/factory/secrets/github_token_bot, else github_token), refuses while an agent run is in flight, runs the tests on the new code,
# rebuilds the sandbox images, restarts the services and checks they stay up. Docker-compose installs use scripts/update.sh instead.
# Testing: SHIKUMI_TARBALL=/path/x.tar.gz uses a local tarball and DRY_RUN=1 prints the service/image commands instead of running them.
set -euo pipefail
ROOT="${SHIKUMI_ROOT:-/srv/factory}"
REPO="${SHIKUMI_REPO:-theninjadojo/software-factory}"
APP="$ROOT/app"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
cd "$ROOT"      # the caller's directory may be unreadable to the factory user (python then fails to start)
run() { if [ -n "${DRY_RUN:-}" ]; then echo "+ $*"; else "$@"; fi; }
die() { echo "$*" >&2; exit 1; }
command -v curl >/dev/null && command -v python3 >/dev/null || die "curl and python3 are required"

TOKEN=""
for f in "${SHIKUMI_TOKEN_FILE:-}" "$ROOT/secrets/github_token_bot" "$ROOT/secrets/github_token"; do
  [ -n "$f" ] && [ -r "$f" ] || continue
  t="$(cat "$f")"
  code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $t" "https://api.github.com/repos/$REPO")"
  [ "$code" = 200 ] && { TOKEN="$t"; break; }
done
api() { curl -fsSL ${TOKEN:+-H "Authorization: Bearer $TOKEN"} -H 'Accept: application/vnd.github+json' "$@"; }

AUTO=""; [ "${1:-}" = "--auto" ] && { AUTO=1; shift; }
HAVE="$(cat "$APP/VERSION" 2>/dev/null || echo none)"
TAG="${1:-}"
if [ -z "$TAG" ]; then
  [ -n "${SHIKUMI_TARBALL:-}" ] && die "give the tag with SHIKUMI_TARBALL"
  TAG="$(api "https://api.github.com/repos/$REPO/releases/latest" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')" \
    || die "no published release found for $REPO (or the token cannot read it)"
fi
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "not a release tag: $TAG"
[ "v$HAVE" = "$TAG" ] && { echo "Already on $TAG."; exit 0; }
if [ -n "$AUTO" ]; then       # unattended: only a newer patch release of the version already running
  python3 - "$HAVE" "$TAG" <<'PY' || { echo "Automatic update: $TAG is not a newer patch of v$HAVE, leaving it for a person (run update-native.sh by hand)."; exit 0; }
import sys
have, new = (tuple(int(x) for x in v.lstrip("v").split(".")) for v in sys.argv[1:3])
sys.exit(0 if new[:2] == have[:2] and new[2] > have[2] else 1)
PY
fi
echo "Updating: v$HAVE -> $TAG"

if [ -z "${DRY_RUN:-}" ]; then            # never restart over a run in flight (a restart kills it)
  N="$(podman ps -q --filter 'name=^factory-' | wc -l)"
  M="$(python3 - "$ROOT/state/factory.db" <<'PY'
import sqlite3, sys
try:
    db = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True, timeout=5)
    print(db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0])
except Exception:
    print(0)
PY
)"
  if [ "$N" != 0 ] || [ "$M" != 0 ]; then
    [ -n "$AUTO" ] && { echo "An agent run is in flight ($N sandboxes, $M runs marked running): will try again at the next timer."; exit 0; }
    die "An agent run is in flight ($N sandboxes, $M runs marked running). Try again when it finishes."
  fi
fi

TMP="$(mktemp -d "$ROOT/state/update.XXXXXX")"; trap 'rm -rf "$TMP"' EXIT
if [ -n "${SHIKUMI_TARBALL:-}" ]; then cp "$SHIKUMI_TARBALL" "$TMP/src.tgz"
else api -o "$TMP/src.tgz" "https://api.github.com/repos/$REPO/tarball/$TAG" || die "could not download $TAG"; fi
mkdir "$TMP/src" && tar -C "$TMP/src" --strip-components=1 -xzf "$TMP/src.tgz"
[ "v$(cat "$TMP/src/VERSION" 2>/dev/null)" = "$TAG" ] || die "the downloaded source is not $TAG (its VERSION file says '$(cat "$TMP/src/VERSION" 2>/dev/null)')"

# test the new code before touching the running install
( cd "$TMP/src" && python3 -m unittest discover -s tests > "$TMP/tests.log" 2>&1 ) || { tail -25 "$TMP/tests.log"; die "TESTS FAILED on $TAG: nothing was changed"; }
tail -3 "$TMP/tests.log"

PARTS=(factory tests sandbox deploy worker config.example.toml VERSION)
rm -rf "$APP.prev"; mkdir "$APP.prev"
for p in "${PARTS[@]}"; do [ -e "$APP/$p" ] && cp -a "$APP/$p" "$APP.prev/"; done
for e in factory-agent factory-render factory-screens; do podman image exists "$e" && run podman tag "$e" "$e:previous" || true; done
for p in "${PARTS[@]}"; do rm -rf "$APP/$p"; [ -e "$TMP/src/$p" ] && cp -a "$TMP/src/$p" "$APP/$p"; done

rollback() {
  echo "The new version did not come up healthy: rolling back to v$HAVE" >&2
  [ -z "${DRY_RUN:-}" ] || return 0
  for p in "${PARTS[@]}"; do rm -rf "$APP/$p"; [ -e "$APP.prev/$p" ] && cp -a "$APP.prev/$p" "$APP/$p"; done
  for e in factory-agent factory-render factory-screens; do podman image exists "$e:previous" && podman tag "$e:previous" "$e" || true; done
  systemctl --user restart factory.service || true
  systemctl --user is-enabled factory-ui.service >/dev/null 2>&1 && systemctl --user restart factory-ui.service || true
  exit 1
}

cd "$APP"
run podman build -q -t factory-agent -f sandbox/Dockerfile sandbox || rollback
run podman build -q -t factory-render -f sandbox/render/Dockerfile sandbox/render || rollback
[ -f sandbox/screens/Dockerfile ] && { run podman build -q -t factory-screens -f sandbox/screens/Dockerfile sandbox/screens || rollback; }
run systemctl --user restart factory.service
systemctl --user is-enabled factory-workers.service >/dev/null 2>&1 && run systemctl --user disable --now factory-workers.service   # the UI runs the worker API now
systemctl --user is-enabled factory-ui.service >/dev/null 2>&1 && run systemctl --user restart factory-ui.service
if [ -z "${DRY_RUN:-}" ]; then
  sleep 10
  for u in factory-proxy factory factory-ui; do systemctl --user is-enabled $u.service >/dev/null 2>&1 || continue
    systemctl --user is-active --quiet $u.service || rollback; done
fi
if [ "$HAVE" = none ]; then echo "Now on $TAG."; else echo "Now on $TAG. Rollback with: $APP/deploy/update-native.sh v$HAVE   (read the release notes first)"; fi
