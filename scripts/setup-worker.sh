#!/usr/bin/env bash
# One-command setup of a verification worker on THIS machine (a Mac, or a Linux box with the tools a build needs). docs/workers.md
#   ./scripts/setup-worker.sh                       # asks for what it needs
#   FACTORY_URL=https://factory.example:8788 WORKER_TOKEN=... ./scripts/setup-worker.sh     # non-interactive (no prompts)
# Get the token on the factory host:  python3 -m factory.ctl workers add <name>   (or FACTORY_WORKERS=1 ./scripts/setup.sh)
# Optional environment: WORKER_RECIPES ("web", "screens", "android", "ios" or a list; default web), WORKER_PLATFORM (default: macos or linux),
#   for ios (a Mac with Tart): WORKER_IOS_SCHEME and WORKER_IOS_PROJECT (or WORKER_IOS_WORKSPACE), WORKER_IOS_DESTINATION,
#   WORKER_IOS_PULL=1 to download the Xcode image (about 30 GB) as VM "shikumi-ios",
#   WORKER_GIT_URL (default https://github.com/{repo}.git; use git@github.com:{repo}.git for SSH keys), SHIKUMI_REPO (image owner),
#   SKIP_IMAGE=1, SKIP_SERVICE=1, SKIP_CHECK=1, SKIP_NODE=1 (web and screens get a Node of the worker's own in tools/node unless the machine has one).
# The worker runs agent-written code: use a dedicated unprivileged account or a throwaway VM, with no credentials on it except a
# read-only way to clone the repos it verifies. Nothing is changed outside this folder except the service file.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$PWD"
say() { printf '\n== %s\n' "$*"; }
die() { echo "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing: $1"; }

need python3; need git
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die "python3 3.11 or newer is required (the worker reads TOML with the standard library)"
OS="$(uname -s)"
PLATFORM="${WORKER_PLATFORM:-$([ "$OS" = Darwin ] && echo macos || echo linux)}"
[[ "$PLATFORM" =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]] || die "WORKER_PLATFORM must be lowercase letters, digits and dashes"
RECIPES="${WORKER_RECIPES:-web}"
for r in $RECIPES; do [[ "$r" == web || "$r" == screens || "$r" == android || "$r" == ios ]] || die "unknown recipe '$r' (this release ships: web, screens, android, ios)"; done
ENGINE="$(command -v docker >/dev/null 2>&1 && echo docker || { command -v podman >/dev/null 2>&1 && echo podman; } || true)"
case " $RECIPES " in *" android "*) [ -n "$ENGINE" ] || die "the android recipe needs Docker (or Podman): it builds inside a container";; esac

if [[ " $RECIPES " == *" ios "* ]]; then
  command -v tart >/dev/null 2>&1 || die "the ios recipe needs Tart (macOS on Apple Silicon): brew install cirruslabs/cli/tart"
  [[ "${WORKER_IOS_SCHEME:-}" =~ ^[A-Za-z0-9_.-]+$ ]] || die "the ios recipe needs WORKER_IOS_SCHEME (the Xcode scheme to test: letters, digits, dot, dash, underscore)"
  [[ -n "${WORKER_IOS_PROJECT:-}" || -n "${WORKER_IOS_WORKSPACE:-}" ]] || die "the ios recipe needs WORKER_IOS_PROJECT (App.xcodeproj) or WORKER_IOS_WORKSPACE (App.xcworkspace)"
  [[ -z "${WORKER_IOS_PROJECT:-}" || -z "${WORKER_IOS_WORKSPACE:-}" ]] || die "give WORKER_IOS_PROJECT or WORKER_IOS_WORKSPACE, not both"
  for v in "${WORKER_IOS_PROJECT:-}" "${WORKER_IOS_WORKSPACE:-}"; do
    [[ -z "$v" || ( "$v" =~ ^[A-Za-z0-9_./-]+$ && "$v" != /* && "$v" != *..* ) ]] || die "the ios project or workspace must be a plain relative path"; done
  DEST_RE='^[A-Za-z0-9=,._ -]+$'          # in a variable: a space inside a bracket expression is not safe to write inline
  [[ "${WORKER_IOS_DESTINATION:-platform=iOS Simulator,name=iPhone 15}" =~ $DEST_RE ]] || die "WORKER_IOS_DESTINATION has characters it cannot have"
fi

say "The factory"
URL="${FACTORY_URL:-}"
[ -n "$URL" ] || { [ -f worker.toml ] && URL="(kept)"; } || read -rp "Worker API URL of the factory (for example https://factory.example:8788, or http://127.0.0.1:8788 through an SSH tunnel): " URL
[[ "$URL" == "(kept)" || "$URL" =~ ^https?://[^[:space:]]+$ ]] || die "FACTORY_URL must start with http:// or https://"

say "Token (file, mode 0600)"
mkdir -p secrets work service
chmod 700 secrets
if [ -s secrets/token ] && [ -z "${WORKER_TOKEN:-}" ]; then echo "secrets/token exists: keeping it"; else
  T="${WORKER_TOKEN:-}"; [ -n "$T" ] || { read -rsp "Worker token (from: python3 -m factory.ctl workers add <name>): " T; echo; }
  [[ "$T" =~ ^[^[:space:]]{24,}$ ]] || die "that does not look like a worker token (24 or more characters, no spaces)"
  ( umask 077; printf %s "$T" > secrets/token ); chmod 600 secrets/token
fi

say "Config"
DEFAULT_GIT_URL='https://github.com/{repo}.git'      # in a variable: inside ${VAR:-...} the first } would end the default early
if [ -f worker.toml ]; then echo "worker.toml exists: keeping it"; else
  IOS_SCHEME="${WORKER_IOS_SCHEME:-}" IOS_PROJECT="${WORKER_IOS_PROJECT:-}" IOS_WORKSPACE="${WORKER_IOS_WORKSPACE:-}" IOS_DEST="${WORKER_IOS_DESTINATION:-platform=iOS Simulator,name=iPhone 15}" \
  DIR="$DIR" URL="$URL" PLATFORM="$PLATFORM" RECIPES="$RECIPES" GIT_URL="${WORKER_GIT_URL:-$DEFAULT_GIT_URL}" python3 - <<'PY'
import os
d, r = os.environ["DIR"], os.environ["RECIPES"].split()
q = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
out = f"""# Written by scripts/setup-worker.sh. Recipes are defined HERE, on the worker: the factory only names one (docs/workers.md).
server = {q(os.environ['URL'].rstrip('/'))}
token_file = {q(d + '/secrets/token')}
platform = {q(os.environ['PLATFORM'])}
poll_seconds = 5
git_url = {q(os.environ['GIT_URL'])}      # clone with THIS machine's own credentials (read-only)
work_dir = {q(d + '/work')}
"""
if "web" in r:
    out += f"""
[recipes.web-test]
command = [{q(d + '/worker/recipes/web-test.sh')}]
timeout_seconds = 1800
artifacts = ["test-results/**/*.png"]
"""
if "screens" in r:
    out += f"""
[recipes.playwright-screens]
command = [{q(d + '/worker/recipes/playwright-screens.sh')}]
timeout_seconds = 3600
artifacts = ["screens-out/*.png"]
"""
if "android" in r:
    out += f"""
[recipes.android-test]
command = [{q(d + '/worker/recipes/android-test.sh')}]
timeout_seconds = 3600
artifacts = ["build/screens/*.png"]
"""
if "ios" in r:
    target = ["--workspace", os.environ["IOS_WORKSPACE"]] if os.environ["IOS_WORKSPACE"] else ["--project", os.environ["IOS_PROJECT"]]
    args = [d + "/worker/recipes/ios-test.sh", "--scheme", os.environ["IOS_SCHEME"], *target, "--destination", os.environ["IOS_DEST"]]
    out += f"""
[recipes.ios-test]
command = [{", ".join(q(a) for a in args)}]
timeout_seconds = 3600
artifacts = ["build/screens/*.png"]
"""
open("worker.toml", "w").write(out)
PY
  chmod 600 worker.toml; echo "wrote worker.toml (platform: $PLATFORM, recipes: $RECIPES)"
fi

if [[ " $RECIPES " == *" web "* || " $RECIPES " == *" screens "* ]] && [ -z "${SKIP_NODE:-}" ]; then
  say "Node.js (the web and screens recipes)"
  if [ -x tools/node/bin/node ]; then echo "tools/node exists ($(tools/node/bin/node --version)): keeping it"
  elif command -v node >/dev/null && command -v npm >/dev/null && node -e 'process.exit(+process.versions.node.split(".")[0] >= 18 ? 0 : 1)' 2>/dev/null \
       && { command -v corepack >/dev/null || command -v pnpm >/dev/null; }; then
    echo "this machine's Node $(node --version) will do: not installing another (SKIP_NODE=1 also skips this step)"
  else
    echo "no usable Node.js here (18 or newer, with npm and corepack or pnpm): installing one of the worker's own into tools/node"
    ./worker/install-node.sh || echo "(could not install it: the recipes stay switched off until Node.js is available; retry with ./worker/install-node.sh)"
  fi
fi

if [ -z "${SKIP_IMAGE:-}" ] && [[ " $RECIPES " == *" android "* ]]; then
  say "Android build image (factory-android)"
  if "$ENGINE" image inspect factory-android:latest >/dev/null 2>&1; then echo "factory-android:latest exists: keeping it"; else
    REPO="${SHIKUMI_REPO:-theninjadojo/software-factory}"; OWNER="$(echo "${REPO%%/*}" | tr 'A-Z' 'a-z')"
    if [ -f VERSION ] && "$ENGINE" pull "ghcr.io/$OWNER/shikumi-android:v$(cat VERSION)"; then
      "$ENGINE" tag "ghcr.io/$OWNER/shikumi-android:v$(cat VERSION)" factory-android:latest
    else
      echo "no prebuilt image available: building from sandbox/android (a few GB of downloads, several minutes)"
      "$ENGINE" build -t factory-android sandbox/android
    fi
  fi
fi

if [ -z "${SKIP_IMAGE:-}" ] && [[ " $RECIPES " == *" ios "* ]]; then
  say "iOS golden image (Tart VM shikumi-ios)"
  if tart list 2>/dev/null | awk '{print $2}' | grep -qx shikumi-ios; then echo "VM shikumi-ios exists: keeping it"
  elif [ -n "${WORKER_IOS_PULL:-}" ]; then
    echo "downloading the Xcode image (about 30 GB; this takes a while)"
    tart clone ghcr.io/cirruslabs/macos-sonoma-xcode:latest shikumi-ios
  else
    echo "No VM named shikumi-ios yet. Create it once (about 30 GB), or re-run with WORKER_IOS_PULL=1:"
    echo "    tart clone ghcr.io/cirruslabs/macos-sonoma-xcode:latest shikumi-ios"
    echo "Jobs fail with 'no VM image named shikumi-ios' until it exists. See docs/workers.md, \"iOS golden image\"."
  fi
fi

say "Service"
PY="$(command -v python3)"
if [ "$OS" = Darwin ]; then UNIT=service/com.shikumi.worker.plist; else UNIT=service/shikumi-worker.service; fi
sed "s|@DIR@|$DIR|g; s|@PYTHON@|$PY|g; s|@PATH@|$PATH|g" "worker/$UNIT" > "$UNIT"
echo "wrote $UNIT"
if [ -n "${SKIP_SERVICE:-}" ]; then echo "SKIP_SERVICE set: not installing it. Run by hand: $PY worker/worker.py --config worker.toml"
elif [ "$OS" = Darwin ]; then
  mkdir -p "$HOME/Library/LaunchAgents"; cp "$UNIT" "$HOME/Library/LaunchAgents/com.shikumi.worker.plist"
  launchctl bootout "gui/$(id -u)/com.shikumi.worker" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.shikumi.worker.plist" && echo "started (launchd: com.shikumi.worker; log: $DIR/worker.log)"
elif command -v systemctl >/dev/null 2>&1; then
  mkdir -p "$HOME/.config/systemd/user"; cp "$UNIT" "$HOME/.config/systemd/user/shikumi-worker.service"
  systemctl --user daemon-reload && systemctl --user enable --now shikumi-worker.service && echo "started (systemd --user: shikumi-worker; log: journalctl --user -u shikumi-worker)"
  echo "To keep it running after you log out:  loginctl enable-linger $USER"
else echo "no launchd or systemd found: run by hand: $PY worker/worker.py --config worker.toml"; fi

if [ -z "${SKIP_CHECK:-}" ]; then
  say "Check"
  python3 worker/worker.py --config worker.toml --check || echo "(fix the lines marked FAIL, then run: python3 worker/worker.py --config worker.toml --check)"
fi
cat <<MSG

Done. This machine now polls the factory for verification jobs.
  Config: $DIR/worker.toml   Token: $DIR/secrets/token   Update: ./scripts/update-worker.sh
  The worker must be able to CLONE the repos it verifies: set up git credentials (a read-only token, or an SSH key with WORKER_GIT_URL).
  On the factory, add checks (Settings -> Workers, or [[workers.checks]] in config.toml) that name a recipe here:
    web-test (web) / android-test (android) / ios-test (ios). The Settings -> Workers page shows this worker as online within seconds.
MSG
