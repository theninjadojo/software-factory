#!/usr/bin/env bash
# Checks templates/kinds/vite-react in place, as its own CI does: install from the lockfile, lint, type-check,
# component tests, build, and the Playwright end-to-end tests against `vite preview` (the API is faked in the browser).
# Needs Node 24+; no API or database.
set -euo pipefail
cd "$(dirname "$0")/../kinds/vite-react"

npm ci
npm run lint -- --max-warnings=0
npm run typecheck
npm test
npm run build
if [ -n "${CI:-}" ]; then npx playwright install --with-deps chromium; else npx playwright install chromium; fi
npm run test:e2e
