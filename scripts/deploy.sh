#!/usr/bin/env bash
# Deploy this checkout to a native (systemd + rootless Podman) install over SSH.
#   scripts/deploy.sh user@host            # code only
#   scripts/deploy.sh user@host --image    # also rebuild the sandbox image (needed when sandbox/ changed)
# Assumes the install layout from docs/operations.md: app in /srv/factory/app owned by the "factory" user, and that
# you can run `sudo -n -u factory`. Refuses to restart while an agent run is in flight (a restart would kill it).
set -euo pipefail
TARGET="${1:?usage: deploy.sh user@host [--image]}"; REBUILD="${2:-}"
cd "$(dirname "$0")/.."
# scripts/ and .github/ go too: the tests that run on the host before the restart check the install and release scripts.
tar -czf /tmp/sf-deploy.tgz factory tests sandbox deploy worker scripts .github config.example.toml .env.example VERSION
scp -q /tmp/sf-deploy.tgz "$TARGET:/tmp/sf-deploy.tgz"; rm -f /tmp/sf-deploy.tgz
ssh "$TARGET" "cat > /tmp/sf-deploy.sh && chmod 755 /tmp/sf-deploy.sh && sudo -n -u factory /tmp/sf-deploy.sh $REBUILD; rm -f /tmp/sf-deploy.sh /tmp/sf-deploy.tgz" <<'REMOTE'
#!/bin/bash
set -e
export XDG_RUNTIME_DIR=/run/user/$(id -u)
cd /srv/factory
RUNNING=$(podman ps -q --filter 'name=^factory-' | wc -l); echo "running sandboxes: $RUNNING"
# A run is also in flight while it clones the repositories, before its sandbox exists: the database knows about it from its first moment.
DBRUNS=$(python3 -c "
import sqlite3,sys
try:
    db = sqlite3.connect('file:/srv/factory/state/factory.db?mode=ro', uri=True, timeout=5)
    print(db.execute(\"SELECT COUNT(*) FROM runs WHERE status='running'\").fetchone()[0])
except Exception:
    print(0)
"); echo "runs marked running: $DBRUNS"
[ "$RUNNING" = "0" ] && [ "$DBRUNS" = "0" ] || { echo "ABORT: an agent run is in flight; try again when it finishes"; exit 1; }
mkdir state/fd && tar -C state/fd -xzf /tmp/sf-deploy.tgz
rm -rf app/factory app/tests app/sandbox app/deploy app/worker app/scripts app/.github
cp -r state/fd/factory state/fd/tests state/fd/sandbox state/fd/deploy state/fd/worker state/fd/scripts state/fd/.github state/fd/config.example.toml state/fd/.env.example state/fd/VERSION app/ && rm -rf state/fd
cd app
if ! python3 -m unittest discover -s tests > /tmp/sf-tests.log 2>&1; then tail -25 /tmp/sf-tests.log; echo 'TESTS FAILED: not restarting (the new code is on disk but the running service is unchanged)'; exit 1; fi
tail -3 /tmp/sf-tests.log
if [ "$1" = "--image" ]; then podman build -q -t factory-agent -f sandbox/Dockerfile sandbox && podman build -q -t factory-render -f sandbox/render/Dockerfile sandbox/render && podman build -q -t factory-screens -f sandbox/screens/Dockerfile sandbox/screens; fi
systemctl --user restart factory.service
# The UI runs no agents, so restarting it is always safe; without this it keeps serving the old code.
if systemctl --user is-enabled factory-ui.service >/dev/null 2>&1; then systemctl --user restart factory-ui.service; fi
# The UI now runs the worker API itself: retire the separate unit from older installs so the two do not fight over the port.
if systemctl --user is-enabled factory-workers.service >/dev/null 2>&1; then systemctl --user disable --now factory-workers.service; fi
sleep 8
systemctl --user is-active factory-proxy.service factory.service factory-ui.service
journalctl --user -u factory.service --since "-12s" --no-pager | grep -v "systemd\|podman\[" | cut -c1-200
REMOTE
