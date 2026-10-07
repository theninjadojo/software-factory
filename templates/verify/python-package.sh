#!/usr/bin/env bash
# Checks the python-package kind in place, as its CI does. Needs uv (which downloads the Python versions it is missing).
set -euo pipefail
cd "$(dirname "$0")/../kinds/python-package"

uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
for python in 3.12 3.13 3.14; do
  uv run --locked --python "$python" pytest
done
uv sync --locked   # back to the default Python
rm -rf dist
uv build
uv run --isolated --no-project --with dist/*.whl shikumi-app hello verify
