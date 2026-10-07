#!/usr/bin/env bash
# Checks the expo kind in place, as its CI does: install, lint, type-check, tests, expo-doctor, web bundle.
# Maestro flows are not run: they need a simulator.
set -euo pipefail
cd "$(dirname "$0")/../kinds/expo"

npm ci
npm run lint
npm run typecheck
npm test -- --ci
npx expo-doctor
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT
npx expo export --platform web --output-dir "$out"
