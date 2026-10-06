# Shared by the JavaScript recipes (web-test.sh, playwright-screens.sh): sourced, never run. docs/workers.md, "Tools the recipes need"
#
# Puts the Node the worker installed for itself (tools/node, by worker/install-node.sh) first on PATH, so a machine without a usable Node
# still works; with no tools/node the machine's own Node is used. Then the package-manager helpers and the preflight.
WORKER_HOME=$(cd "$(dirname "$0")/../.." 2>/dev/null && pwd)
if [ -n "$WORKER_HOME" ] && [ -x "$WORKER_HOME/tools/node/bin/node" ]; then PATH="$WORKER_HOME/tools/node/bin:$PATH"; export PATH; fi

have() { command -v "$1" >/dev/null 2>&1; }
node_ok() { have node && node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 18 ? 0 : 1)' 2>/dev/null; }

# pnpm through corepack when this Node has it (Node 25 and newer no longer ships it), else pnpm itself; yarn itself, else through corepack.
pnpm_run() { if have corepack; then corepack pnpm "$@"; elif have pnpm; then pnpm "$@"; else echo "[recipe] neither corepack nor pnpm is installed" >&2; return 127; fi; }
yarn_run() { if have yarn; then yarn "$@"; elif have corepack; then corepack yarn "$@"; else echo "[recipe] neither yarn nor corepack is installed" >&2; return 127; fi; }

# js_preflight: print what is missing, one line each, and return 3 when anything is (0 when this machine can run a JavaScript recipe).
# The worker runs it (--preflight) to decide whether to take the recipe's jobs at all, and says why when it does not.
js_preflight() {
  bad=0
  fix="run worker/install-node.sh in the worker folder (it installs a Node of its own, nothing system-wide)"
  node_ok || { echo "Node.js 18 or newer is not installed: $fix"; bad=3; }
  have npm || { echo "npm is not installed: $fix"; bad=3; }
  if ! have corepack && ! have pnpm; then echo "neither corepack nor pnpm is installed (pnpm projects need one): $fix"; bad=3; fi
  return $bad
}
