#!/usr/bin/env bash
# Update a release install of a Shikumi verification worker to the latest (or a given) release, and roll back if it does not come up.
#   ./scripts/update-worker.sh            # latest release
#   ./scripts/update-worker.sh v0.2.1     # a specific release (also how you go back)
# Run it in the install folder, on the worker machine. It refuses while a job is running (a restart would lose it; the factory would
# re-queue it, wasting the work): FORCE=1 overrides. worker.toml, secrets/ and work/ are never touched.
# Testing: SHIKUMI_ASSET_BASE=file:///dir serves the release files locally; SKIP_SERVICE=1 and SKIP_IMAGE=1 skip those steps.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$PWD"
REPO="${SHIKUMI_REPO:-$(sed -n 's/^SHIKUMI_REPO=//p' .env 2>/dev/null | tail -1 || true)}"; REPO="${REPO:-theninjadojo/software-factory}"   # .env only exists if SHIKUMI_REPO was set at install
OWNER="$(echo "${REPO%%/*}" | tr 'A-Z' 'a-z')"
die() { echo "$*" >&2; exit 1; }
command -v curl >/dev/null || die "curl is required"; command -v python3 >/dev/null || die "python3 is required"
[ -f worker.toml ] || die "no worker.toml here: run this in the install folder (the one scripts/setup-worker.sh created)"

HAVE="$(cat VERSION 2>/dev/null || echo unknown)"; TAG="${1:-}"
if [ -z "$TAG" ]; then
  TAG="$(curl -fsS -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$REPO/releases/latest" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')" \
    || die "could not read the latest release of $REPO (offline, or none published yet?)"
fi
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "not a release tag: $TAG"
[ "v$HAVE" = "$TAG" ] && { echo "Already on $TAG."; exit 0; }
echo "Updating the worker: v$HAVE -> $TAG"

if [ -z "${FORCE:-}" ] && ls -d work/factory-job-* >/dev/null 2>&1; then die "A job is running (work/factory-job-*). Try again when it finishes, or set FORCE=1."; fi

BASE="${SHIKUMI_ASSET_BASE:-https://github.com/$REPO/releases/download/$TAG}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
for f in VERSION shikumi-worker.tar.gz setup-worker.sh update-worker.sh; do curl -fsSL "$BASE/$f" -o "$TMP/$f" || die "could not download $f from release $TAG"; done
[ "v$(cat "$TMP/VERSION")" = "$TAG" ] || die "the release files do not match $TAG"
mkdir "$TMP/new" && tar -xzf "$TMP/shikumi-worker.tar.gz" -C "$TMP/new" || die "the worker archive is damaged"
[ -f "$TMP/new/worker/worker.py" ] || die "the worker archive has no worker/worker.py"

BK=".shikumi-worker-backup"; rm -rf "$BK"; mkdir -p "$BK"
cp -R worker VERSION "$BK"/; [ -d sandbox ] && cp -R sandbox "$BK"/ || true
restart() {
  [ -z "${SKIP_SERVICE:-}" ] || return 0
  if [ "$(uname -s)" = Darwin ]; then launchctl kickstart -k "gui/$(id -u)/com.shikumi.worker" 2>/dev/null || true
  elif command -v systemctl >/dev/null 2>&1; then systemctl --user restart shikumi-worker.service 2>/dev/null || true; fi
}
rollback() {
  echo "The new version did not pass its check: rolling back to v$HAVE" >&2
  rm -rf worker sandbox; cp -R "$BK"/worker .; [ -d "$BK/sandbox" ] && cp -R "$BK"/sandbox . || true; cp "$BK"/VERSION VERSION
  restart; exit 1
}
rm -rf worker sandbox; cp -R "$TMP/new/worker" worker; [ -d "$TMP/new/sandbox" ] && cp -R "$TMP/new/sandbox" sandbox || true
chmod +x worker/recipes/*.sh worker/install-node.sh sandbox/android/android-run.sh 2>/dev/null || true
cp "$TMP/VERSION" VERSION
if [ -z "${SKIP_IMAGE:-}" ] && command -v docker >/dev/null 2>&1 && docker image inspect factory-android:latest >/dev/null 2>&1; then
  docker image inspect factory-android:latest >/dev/null 2>&1 && docker tag factory-android:latest factory-android:previous || true
  if docker pull "ghcr.io/$OWNER/shikumi-android:$TAG"; then docker tag "ghcr.io/$OWNER/shikumi-android:$TAG" factory-android:latest
  else echo "(no prebuilt android image for $TAG: keeping the one you have; rebuild with docker build -t factory-android sandbox/android)"; fi
fi
if [ -z "${SKIP_NODE:-}" ] && grep -qE 'web-test\.sh|playwright-screens\.sh' worker.toml 2>/dev/null; then       # a JavaScript recipe on a machine with no Node of its own
  for r in web-test playwright-screens; do
    if grep -q "$r\.sh" worker.toml && [ -f "worker/recipes/$r.sh" ] && ! sh "worker/recipes/$r.sh" --preflight >/dev/null 2>&1; then
      echo "The $r recipe cannot run on this machine yet: installing a Node of the worker's own (worker/install-node.sh)"
      ./worker/install-node.sh || echo "(could not install it: that recipe stays switched off; the Workers page says why)"; break
    fi
  done
fi
restart
sleep "${SETTLE_SECONDS:-3}"
python3 worker/worker.py --config worker.toml --check || rollback
cp "$TMP/setup-worker.sh" scripts/setup-worker.sh; chmod +x scripts/setup-worker.sh
cp "$TMP/update-worker.sh" scripts/update-worker.sh; chmod +x scripts/update-worker.sh      # replaced last: this script is still running
echo "Now on $TAG. Rollback later with: ./scripts/update-worker.sh v$HAVE   (read the release notes first)"
