#!/bin/sh
# Screens recipe: run a project's Playwright suite and keep a screenshot of every test, for the Screens board. docs/workers.md
#
# Runs in the checkout (the worker sets the working directory). This script lives on the WORKER, outside the checkout: nothing in the
# repo can change it. The repo's own scripts and tests do run, as they must: that is the code being shown.
#
#   command = ["/path/to/playwright-screens.sh"]                      in worker.toml
#   command = ["/path/to/playwright-screens.sh", "--dir", "web"]      only the project in a subfolder
#   artifacts = ["screens-out/*.png"]
#
# A repo with no JavaScript Playwright config but a Python suite in tests/e2e (requirements.txt there) runs as pytest instead, in a throwaway
# virtualenv. The suite opts in by writing PNGs to $E2E_SCREENS_DIR (this recipe sets it); a suite that does not still runs, with no images.
# --browser /path/to/chromium: use this browser for that Python path (E2E_BROWSER) and skip downloading one.
#
# Finds every folder that has a Playwright config (or the --dir given), installs with the lockfile, installs Chromium, then runs the
# suite through a wrapper config WRITTEN HERE that turns on a full-page screenshot at the end of every test, whatever the project's own
# config says. Every PNG is copied to screens-out/ under a unique, readable name. The screenshots are returned even when tests fail, so a
# red suite still shows what the screens looked like. Exit 0: the suite passed. Exit 1: install or tests failed. Exit 2: nothing to run.
set -u
. "$(dirname "$0")/lib.sh"
ONLY=""
BROWSER=""
PREFLIGHT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --preflight) PREFLIGHT=1; shift ;;
    --dir) ONLY="${2:-}"; shift 2 ;;
    --browser) BROWSER="${2:-}"; shift 2 ;;
    *) echo "[playwright-screens] unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [ "$PREFLIGHT" = 1 ]; then js_preflight; exit $?; fi      # a repo with only a Python suite needs no Node, but most do; the check is for the common case
case "$ONLY" in /*|*..*) echo "[playwright-screens] --dir must be a plain relative folder" >&2; exit 2 ;; esac
ROOT=$(pwd)
OUT="$ROOT/screens-out"
mkdir -p "$OUT"

config_in() { for f in playwright.config.ts playwright.config.mts playwright.config.js playwright.config.mjs playwright.config.cjs; do [ -f "$1/$f" ] && { echo "$f"; return 0; }; done; return 1; }

if [ -n "$ONLY" ]; then DIRS="$ONLY"
else DIRS=$(find . -maxdepth 3 -name 'playwright.config.*' -not -path '*/node_modules/*' -exec dirname {} \; | sort -u | sed 's#^\./##'); fi

if [ -z "$DIRS" ] && [ -z "$ONLY" ] && [ -f tests/e2e/requirements.txt ]; then
  echo "[playwright-screens] == Python suite in tests/e2e"
  VENV="$ROOT/.screens-venv"
  python3 -m venv "$VENV" && "$VENV/bin/pip" install -q -r tests/e2e/requirements.txt || { echo "[playwright-screens] FAILED: installing the Python suite" >&2; exit 1; }
  if [ -z "$BROWSER" ]; then "$VENV/bin/playwright" install chromium || { echo "[playwright-screens] FAILED: playwright install" >&2; exit 1; }; fi
  status=0
  E2E_SCREENS_DIR="$OUT" E2E_BROWSER="$BROWSER" "$VENV/bin/python" -m pytest tests/e2e -q -p no:cacheprovider || { echo "[playwright-screens] some tests failed (their screenshots are kept)" >&2; status=1; }
  count=$(find "$OUT" -name '*.png' | wc -l)
  echo "[playwright-screens] $count screenshot(s) kept"
  exit "$status"
fi
[ -n "$DIRS" ] || { echo "[playwright-screens] no Playwright config found in this repository" >&2; exit 2; }

slug() { printf '%s' "$1" | tr 'A-Z' 'a-z' | sed 's/[^a-z0-9][^a-z0-9]*/-/g; s/^-//; s/-$//'; }
status=0
count=0

for D in $DIRS; do
  cd "$ROOT/$D" 2>/dev/null || { echo "[playwright-screens] no folder $D in the checkout" >&2; status=2; continue; }
  CFG=$(config_in .) || { echo "[playwright-screens] no Playwright config in $D" >&2; status=2; continue; }
  [ -f package.json ] || { echo "[playwright-screens] no package.json in $D" >&2; status=2; continue; }
  echo "[playwright-screens] == $D ($CFG)"
  if [ -f pnpm-lock.yaml ]; then pnpm_run install --frozen-lockfile
  elif [ -f yarn.lock ]; then yarn_run install --frozen-lockfile
  elif [ -f package-lock.json ] || [ -f npm-shrinkwrap.json ]; then npm ci --no-audit --no-fund
  else echo "[playwright-screens] WARNING: no lockfile, so versions are not pinned"; npm install --no-audit --no-fund
  fi || { echo "[playwright-screens] FAILED: install in $D" >&2; status=1; continue; }
  npx --no-install playwright install chromium || { echo "[playwright-screens] FAILED: playwright install in $D" >&2; status=1; continue; }

  BASE="${CFG%.*}"; EXT="${CFG##*.}"
  SHOT="{ mode: 'on', fullPage: true }"
  case "$EXT" in
    ts|mts|mjs) W="playwright.screens.config.$EXT"; IMPORT="import base from './$CFG';"; [ "$EXT" = ts ] && IMPORT="import base from './$BASE';"; EXPORT="export default" ;;
    *) if grep -q '"type"[[:space:]]*:[[:space:]]*"module"' package.json; then W="playwright.screens.config.mjs"; IMPORT="import base from './$CFG';"; EXPORT="export default"
       else W="playwright.screens.config.cjs"; IMPORT="const m = require('./$CFG'); const base = m.default || m;"; EXPORT="module.exports =" ; fi ;;
  esac
  cat > "$W" <<JS
$IMPORT
const shot = $SHOT;
const b = base;
$EXPORT {
  ...b,
  use: { ...(b.use || {}), screenshot: shot },
  projects: (b.projects || []).map((p) => ({ ...p, use: { ...(p.use || {}), screenshot: shot } })),
  outputDir: 'test-results-screens',
  reporter: 'line',
};
JS
  echo "[playwright-screens] playwright test --config $W"
  npx --no-install playwright test --config "$W" || { echo "[playwright-screens] some tests failed in $D (their screenshots are kept)" >&2; [ "$status" = 0 ] && status=1; }

  PREFIX=""; [ "$D" != "." ] && PREFIX="$(slug "$D")-"
  find test-results-screens -name '*.png' 2>/dev/null | sort | while IFS= read -r f; do
    dir=$(dirname "$f" | sed 's#^test-results-screens/*##'); stem=$(basename "$f" .png)
    label="$PREFIX$(slug "$dir")"; [ "$stem" != "test-finished-1" ] && label="$label-$(slug "$stem")"
    sum=$(printf '%s' "$D/$f" | cksum | cut -d' ' -f1)
    if [ ${#label} -gt 46 ]; then label="$(printf '%s' "$label" | cut -c1-22)-$(printf '%s' "$label" | rev | cut -c1-23 | rev)"; fi   # keep both ends: the project is last
    cp "$f" "$OUT/$label-$sum.png"
  done
  cd "$ROOT"
done

count=$(find "$OUT" -name '*.png' | wc -l)
echo "[playwright-screens] $count screenshot(s) kept"
[ "$status" = 2 ] && [ "$count" = 0 ] && exit 2
exit "$status"
