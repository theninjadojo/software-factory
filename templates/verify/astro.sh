#!/usr/bin/env bash
# Checks the astro kind in place, as its CI does: install, lint, type-check, unit tests, build, budget, links, e2e.
set -euo pipefail
cd "$(dirname "$0")/../kinds/astro"

npm ci
npm run lint
npm run check
npm test
npm run build
npm run budget
npm run links
if [ -n "${CI:-}" ]; then npx playwright install --with-deps chromium; else npx playwright install chromium; fi
npm run test:e2e
