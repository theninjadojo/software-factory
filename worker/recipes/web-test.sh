#!/bin/sh
# Verification recipe: install, build and test a web project, plus Playwright when the project has it. docs/workers.md
#
# Runs in the checkout (the worker sets the working directory). This script lives on the WORKER, outside the checkout: nothing in the
# repo can change it. The repo's own scripts (npm test ...) do run, as they must: that is the code being verified.
#
#   command = ["/path/to/web-test.sh"]                      in worker.toml
#   command = ["/path/to/web-test.sh", "--dir", "web"]      a project in a subfolder (monorepo)
#   artifacts = ["test-results/**/*.png", "playwright-report/**/*.png"]
#
# Steps: install with the lockfile (pnpm, yarn or npm ci), build if there is a build script, test if there is a real test script,
# then `playwright test` if a playwright config exists. Exit 0 only when every step that applies passed; anything else is a failure
# with the step's output above this line. A project this recipe cannot recognise exits 2 (a worker/recipe problem, not a failed test).
set -u
. "$(dirname "$0")/lib.sh"
case " $* " in *" --preflight "*) js_preflight; exit $? ;; esac
DIR=.
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="${2:-}"; shift 2 ;;
    --no-playwright) NO_PW=1; shift ;;
    *) echo "[web-test] unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$DIR" in /*|*..*|"") echo "[web-test] --dir must be a plain relative folder" >&2; exit 2 ;; esac
cd "$DIR" 2>/dev/null || { echo "[web-test] no folder $DIR in the checkout" >&2; exit 2; }
[ -f package.json ] || { echo "[web-test] no package.json in $DIR: this recipe is for JavaScript/TypeScript projects" >&2; exit 2; }

step() { echo "[web-test] == $*"; }
has_script() {   # has_script <name>: a script exists and is not npm's placeholder (sh and grep only: the worker may have no python)
  entry=$(grep -oE "\"$1\"[[:space:]]*:[[:space:]]*\"([^\"\\\\]|\\\\.)+\"" package.json | head -n 1)
  [ -n "$entry" ] && ! printf '%s' "$entry" | grep -q "no test specified"
}
run() { "$@" || { echo "[web-test] FAILED: $*" >&2; exit 1; }; }

if [ -f pnpm-lock.yaml ]; then
  RUNNER="pnpm"; step "pnpm install --frozen-lockfile"; run pnpm_run install --frozen-lockfile
elif [ -f yarn.lock ]; then
  RUNNER="yarn"; step "yarn install --frozen-lockfile"; run yarn_run install --frozen-lockfile
elif [ -f package-lock.json ] || [ -f npm-shrinkwrap.json ]; then
  RUNNER="npm"; step "npm ci"; run npm ci --no-audit --no-fund
else
  RUNNER="npm"; echo "[web-test] WARNING: no lockfile, so versions are not pinned"; step "npm install"; run npm install --no-audit --no-fund
fi

ran=0
if has_script build; then step "$RUNNER run build"; run "$RUNNER" run build; ran=1; fi
if has_script test; then step "$RUNNER test"; run "$RUNNER" test; ran=1; fi

has_playwright() { for f in playwright.config.ts playwright.config.js playwright.config.mjs playwright.config.cjs; do [ -f "$f" ] && return 0; done; return 1; }
if [ -z "${NO_PW:-}" ] && has_playwright; then
  step "playwright install chromium"; run npx --no-install playwright install chromium
  step "playwright test"; run npx --no-install playwright test --reporter=line
  ran=1
fi

if [ "$ran" = 0 ]; then
  echo "[web-test] nothing to run: no build script, no test script and no Playwright config" >&2
  exit 2
fi
echo "[web-test] all steps passed"
