#!/usr/bin/env bash
# Checks templates/kinds/nextjs in place, as its own CI does: install from the lockfile, lint, type-check, unit tests,
# build, and the Playwright end-to-end tests against the built app.
# Needs Node 24+ and DATABASE_URL pointing at a running, empty-or-migrated Postgres, for example:
#   docker run -d --rm -p 55432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=app postgres:18
#   DATABASE_URL=postgres://postgres:postgres@localhost:55432/app templates/verify/nextjs.sh
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL to a running Postgres}"
cd "$(dirname "$0")/../kinds/nextjs"
export NEXT_TELEMETRY_DISABLED=1

npm ci
npm run lint -- --max-warnings=0
npm run typecheck
npm test
npm run build
if [ -n "${CI:-}" ]; then npx playwright install --with-deps chromium; else npx playwright install chromium; fi
npm run test:e2e
