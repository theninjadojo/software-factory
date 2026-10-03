#!/usr/bin/env bash
# Install a Shikumi verification worker from the latest GitHub release (no git checkout, no build):
#   curl -fsSL https://github.com/theninjadojo/software-factory/releases/latest/download/install-worker.sh | bash
# Creates ./shikumi-worker, downloads the worker files, then runs scripts/setup-worker.sh (which asks for the factory URL and token).
set -euo pipefail
REPO="${SHIKUMI_REPO:-theninjadojo/software-factory}"
BASE="${SHIKUMI_ASSET_BASE:-https://github.com/$REPO/releases/latest/download}"
DIR="${SHIKUMI_WORKER_DIR:-shikumi-worker}"
command -v curl >/dev/null || { echo "curl is required"; exit 1; }
command -v tar >/dev/null || { echo "tar is required"; exit 1; }
[ -e "$DIR" ] && { echo "$DIR already exists: to update, run $DIR/scripts/update-worker.sh"; exit 1; }
mkdir -p "$DIR/scripts" && cd "$DIR"
curl -fsSL "$BASE/VERSION" -o VERSION
curl -fsSL "$BASE/shikumi-worker.tar.gz" -o worker.tar.gz
tar -xzf worker.tar.gz && rm worker.tar.gz
for f in setup-worker.sh update-worker.sh; do curl -fsSL "$BASE/$f" -o "scripts/$f"; chmod +x "scripts/$f"; done
chmod +x worker/recipes/*.sh sandbox/android/android-run.sh 2>/dev/null || true
[ -n "${SHIKUMI_REPO:-}" ] && printf 'SHIKUMI_REPO=%s\n' "$SHIKUMI_REPO" > .env
echo "Shikumi worker v$(cat VERSION) downloaded to $PWD"
exec ./scripts/setup-worker.sh
