#!/usr/bin/env bash
# Install Shikumi from its latest GitHub release (no git, no source build):
#   curl -fsSL https://github.com/theninjadojo/software-factory/releases/latest/download/install.sh | bash
# Creates ./shikumi, downloads the release files, then runs scripts/setup.sh (which asks for your repos and keys).
set -euo pipefail
REPO="${SHIKUMI_REPO:-theninjadojo/software-factory}"
BASE="${SHIKUMI_ASSET_BASE:-https://github.com/$REPO/releases/latest/download}"
DIR="${SHIKUMI_DIR:-shikumi}"
command -v curl >/dev/null || { echo "curl is required"; exit 1; }
[ -e "$DIR" ] && { echo "$DIR already exists: to update, run $DIR/scripts/update.sh"; exit 1; }
mkdir -p "$DIR/scripts" && cd "$DIR"
for f in docker-compose.yml config.example.toml VERSION .env.example; do curl -fsSL "$BASE/$f" -o "$f"; done
for f in setup.sh update.sh; do curl -fsSL "$BASE/$f" -o "scripts/$f"; chmod +x "scripts/$f"; done
[ -n "${SHIKUMI_REPO:-}" ] && printf 'SHIKUMI_REPO=%s\n' "$SHIKUMI_REPO" >> .env.example
echo "Shikumi v$(cat VERSION) downloaded to $PWD"
exec ./scripts/setup.sh
