#!/usr/bin/env bash
# Runs the Maestro flows in .maestro/ against the app installed on the running simulator, emulator or device.
# APP_ID defaults to the bundle identifier from app.config.ts.
set -euo pipefail
cd "$(dirname "$0")/.."
app_id="${APP_ID:-$(npx expo config --type public --json | node -e 'let s = ""; process.stdin.on("data", (d) => (s += d)).on("end", () => console.log(JSON.parse(s).ios.bundleIdentifier))')}"
exec maestro test -e APP_ID="$app_id" -e MAESTRO_RUN_ID="$(date +%s)" "${@:-.maestro}"
