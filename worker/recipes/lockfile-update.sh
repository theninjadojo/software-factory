#!/bin/sh
# Lockfile recipe: refresh the lockfiles of a JavaScript repo for packages that were just published. docs/workers.md, factory/lockfiles.py
#
# The factory runs it for a second build (a repo built after the packages it installs were published). The checkout holds the default
# branch plus ONLY the agent's package.json and lockfile changes; the worker returns what this recipe changed in those files, and the
# factory checks that again. Nothing in the repository runs: --lockfile-only never installs packages or runs their scripts.
#
#   [recipes.lockfile-update]
#   command = ["/path/to/worker/recipes/lockfile-update.sh", "--token-file", "/path/to/secrets/npm_token"]
#   # --token-file: a READ-ONLY packages token, mode 0600 (GitHub Packages: a classic token with read:packages only). It is given to
#   # the registry named by --registry (default npm.pkg.github.com) and nowhere else. Without it, only public packages resolve.
#
# Steps, in each folder with a lockfile whose package.json files use one of the published packages (or whose package.json the agent
# changed): `pnpm install --lockfile-only`, then `pnpm update --lockfile-only <those packages>` (newest within each range); npm the same
# with --package-lock-only. yarn is not handled. Exit 0 when every folder was refreshed, 1 when a package manager failed, 2 when the job
# is unusable.
set -u
. "$(dirname "$0")/lib.sh"
case " $* " in *" --preflight "*) js_preflight; exit $? ;; esac
TOKEN_FILE=""; REGISTRY=npm.pkg.github.com
while [ $# -gt 0 ]; do
  case "$1" in
    --token-file) TOKEN_FILE="${2:-}"; shift 2 ;;
    --registry) REGISTRY="${2:-}"; shift 2 ;;
    *) echo "[lockfile] unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$REGISTRY" in ""|*/*|*" "*) echo "[lockfile] --registry must be a host name, like npm.pkg.github.com" >&2; exit 2 ;; esac
[ -n "${FACTORY_JOB_PARAMS:-}" ] && [ -f "$FACTORY_JOB_PARAMS" ] || { echo "[lockfile] no job parameters: run by the factory only" >&2; exit 2; }
node_ok || { echo "[lockfile] Node.js 18 or newer is needed" >&2; exit 2; }

if [ -n "$TOKEN_FILE" ]; then                        # the token goes to its registry only, from a file outside the checkout
  [ -r "$TOKEN_FILE" ] || { echo "[lockfile] cannot read the token file $TOKEN_FILE" >&2; exit 2; }
  TOKEN=$(tr -d ' \n\r' < "$TOKEN_FILE")
  RC=$(mktemp) || exit 2
  chmod 600 "$RC"; trap 'rm -f "$RC"' EXIT
  [ -f "$HOME/.npmrc" ] && cat "$HOME/.npmrc" > "$RC"
  printf '\n//%s/:_authToken=%s\n' "$REGISTRY" "$TOKEN" >> "$RC"
  NPM_CONFIG_USERCONFIG="$RC"; NODE_AUTH_TOKEN="$TOKEN"; export NPM_CONFIG_USERCONFIG NODE_AUTH_TOKEN   # NODE_AUTH_TOKEN: a repo .npmrc may name it
fi

# One line per folder to refresh: "<folder>\t<packages used under it>" ("." is the repository root).
PLAN=$(node - <<'JS'
const fs = require("fs"), path = require("path");
const p = JSON.parse(fs.readFileSync(process.env.FACTORY_JOB_PARAMS, "utf8"));
const safe = (d) => typeof d === "string" && !d.startsWith("/") && !d.split("/").includes("..");
const pkgs = (p.packages || []).filter((n) => typeof n === "string" && /^(@[\w.-]+\/)?[\w.-]+$/.test(n));
const LOCKS = ["pnpm-lock.yaml", "package-lock.json", "npm-shrinkwrap.json"];
const manifests = [];
(function walk(d, depth) {
  if (fs.existsSync(path.join(d, "package.json"))) manifests.push(d);
  if (depth >= 5) return;
  for (const e of fs.readdirSync(d, { withFileTypes: true }))
    if (e.isDirectory() && e.name !== "node_modules" && !e.name.startsWith(".")) walk(d === "." ? e.name : `${d}/${e.name}`, depth + 1);
})(".", 0);
const uses = (d) => {
  try {
    const j = JSON.parse(fs.readFileSync(path.join(d, "package.json"), "utf8"));
    const all = Object.assign({}, j.dependencies, j.devDependencies, j.optionalDependencies, j.peerDependencies);
    return pkgs.filter((n) => n in all);
  } catch { return []; }
};
const lockDir = (d) => {
  for (let c = d; ; c = c.includes("/") ? c.slice(0, c.lastIndexOf("/")) : ".") {
    if (LOCKS.some((l) => fs.existsSync(path.join(c, l)))) return c;
    if (c === ".") return null;
  }
};
const plan = new Map();
const changed = new Set((p.dirs || []).filter(safe).map((d) => d || "."));
for (const d of manifests) {
  const used = uses(d);
  if (!used.length && !changed.has(d)) continue;
  const l = lockDir(d);
  if (l === null) continue;
  plan.set(l, new Set([...(plan.get(l) || []), ...used]));
}
for (const [d, s] of plan) console.log(`${d}\t${[...s].join(" ")}`);
JS
) || { echo "[lockfile] could not read the job or the repository's package.json files" >&2; exit 2; }
[ -n "$PLAN" ] || { echo "[lockfile] no lockfile uses the published packages: nothing to refresh"; exit 0; }

run() { echo "[lockfile] $*"; "$@" || { echo "[lockfile] FAILED: $*" >&2; exit 1; }; }
TAB=$(printf '\t')
echo "$PLAN" | while IFS="$TAB" read -r dir pkgs; do
  (
    cd "$dir" || exit 1
    echo "##progress refreshing $dir"
    if [ -f pnpm-lock.yaml ]; then
      rec=""; [ -f pnpm-workspace.yaml ] && rec="--recursive"
      run pnpm_run install --lockfile-only --ignore-scripts
      # shellcheck disable=SC2086
      [ -z "$pkgs" ] || run pnpm_run update --lockfile-only $rec $pkgs
    else
      run npm install --package-lock-only --ignore-scripts --no-audit --no-fund
      # shellcheck disable=SC2086
      [ -z "$pkgs" ] || run npm update --package-lock-only --ignore-scripts --no-audit --no-fund $pkgs
    fi
  ) || exit 1
done || exit 1
echo "[lockfile] done"
