#!/usr/bin/env bash
# One-command first-time setup for the Docker install: directories, config, secrets, images, UI password, labels.
#   ./scripts/setup.sh                      # asks for what it needs
#   GITHUB_TOKEN=... ANTHROPIC_API_KEY=... FACTORY_REPOS="org/a org/b" ./scripts/setup.sh   # non-interactive (no prompts)
# Optional environment: FACTORY_HOME (default /srv/factory), ENGINE (docker|podman, default: what is installed),
#   SKIP_BUILD=1, SKIP_START=1, SKIP_LABELS=1, FACTORY_UI_PASSWORD (skips the password prompt).
# Verification workers (optional, docs/workers.md): FACTORY_WORKERS=1 turns on the worker API, and creates a token for FACTORY_WORKER_NAME
#   (default "worker"). FACTORY_WORKER_CHECKS="org/app:web-test:any org/ios:ios-test:macos" adds [[workers.checks]] (repo:recipe:platform).
# More environment: SKIP_CHECKS=1 (do not call GitHub / Anthropic to validate the keys, and skip the health wait and doctor at the end), UI_ACCESS=local|network and FACTORY_UI_HOST=<name or ip>
#   (who opens the web UI), CLAUDE_CODE_OAUTH_TOKEN (a subscription token instead of an API key).
# It starts the factory in DRY-RUN: it logs what it would do and touches nothing until you press "Go live" on the home screen of the UI.
set -euo pipefail
cd "$(dirname "$0")/.."
say() { printf '\n== %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1"; exit 1; }; }

ENGINE="${ENGINE:-$(command -v docker >/dev/null 2>&1 && echo docker || { command -v podman >/dev/null 2>&1 && echo podman; } || true)}"
[ -n "$ENGINE" ] || { echo "Install Docker (or Podman) first."; exit 1; }
need python3
HOME_DIR="${FACTORY_HOME:-/srv/factory}"
[ "$ENGINE" = docker ] || { echo "setup.sh drives the Docker compose install. For Podman use the native install (docs/operations.md)."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "needs the docker compose plugin"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is installed but not reachable. Start it, and make sure your user may use it (docker group), then run this again."; exit 1; }
INTERACTIVE=1; [ -t 0 ] || INTERACTIVE=""
ask() {  # prompt, default -> answer (the default when not interactive or on Enter)
  local a=""; [ -n "$INTERACTIVE" ] && read -rp "$1" a || true; echo "${a:-$2}"; }
cat <<'WELCOME'

Shikumi setup. In a few minutes you will have:
  1. a GitHub token that can read your repos,
  2. model credentials for the agents (an Anthropic API key, or a Claude subscription token),
  3. a password for the web UI,
  4. everything running in DRY-RUN: it only logs what it would do. Nothing is written to GitHub until you press "Go live" in the UI.
WELCOME

say "Directories under $HOME_DIR"
# sudo only when this user cannot write there (the containers run as uid 1000, so that user must own the folders)
S=""; mkdir -p "$HOME_DIR" 2>/dev/null && [ -w "$HOME_DIR" ] || S=sudo
$S mkdir -p "$HOME_DIR"/{secrets,state,work,run}
# fix the owner whenever something under there is not uid 1000's (also when we ARE uid 1000: a folder left over from an
# earlier run as root, or made by the engine, would otherwise stay unwritable for the containers)
if [ -n "$(find "$HOME_DIR" ! -user 1000 -print -quit 2>/dev/null)" ]; then
  if ! chown -R 1000:1000 "$HOME_DIR" 2>/dev/null; then sudo chown -R 1000:1000 "$HOME_DIR"; fi
fi
$S chmod 700 "$HOME_DIR/secrets"

say "Config"
mkdir -p config
if [ -f config/config.toml ]; then echo "config/config.toml exists: keeping it"; else
  REPOS="${FACTORY_REPOS:-}"
  [ -n "$REPOS" ] || read -rp "GitHub repos the factory should work on (owner/name, space separated): " REPOS
  [ -n "$REPOS" ] || { echo "at least one repo is needed"; exit 1; }
  for r in $REPOS; do [[ "$r" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "not a repo name: $r"; exit 1; }; done
  REPOS="$REPOS" HOME_DIR="$HOME_DIR" python3 - <<'PY'
import os, re
s = open("config.example.toml").read()
repos = ", ".join('"%s"' % r for r in os.environ["REPOS"].split())
s = re.sub(r'(?m)^repos = \[.*\]( +#.*)?$', "repos = [%s]" % repos, s, count=1)
s = re.sub(r'(?m)^engine = "podman".*$', 'engine = "docker"', s, count=1)
s = s.replace("/srv/factory", os.environ["HOME_DIR"])
# the example's sample project lists made-up repos: drop it so only the repos above are used
i = s.find("[[projects]]")
if i != -1:
    j = s.find("\n]\n", i)
    s = s[:i] + "# [[projects]]  (see config.example.toml: repos that work together as one project)\n" + s[j + 3:] if j != -1 else s
open("config/config.toml", "w").write(s)
PY
  echo "wrote config/config.toml (dry_run = true)"
fi

say ".env"
if [ ! -f .env ]; then
  cp .env.example .env
  GID="$(stat -c %g /var/run/docker.sock 2>/dev/null || echo "")"
  sed -i "s|^DOCKER_GID=.*|DOCKER_GID=$GID|; s|^FACTORY_HOME=.*|FACTORY_HOME=$HOME_DIR|" .env
  echo "wrote .env (DOCKER_GID=$GID)"
else echo ".env exists: keeping it"; fi

if [ -n "${FACTORY_WORKERS:-}" ]; then
  say "Verification workers"
  WNAME="${FACTORY_WORKER_NAME:-worker}"
  [[ "$WNAME" =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]] || { echo "FACTORY_WORKER_NAME must be lowercase letters, digits and dashes"; exit 1; }
  for c in ${FACTORY_WORKER_CHECKS:-}; do [[ "$c" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:[a-z0-9][a-z0-9-]*:[a-z0-9][a-z0-9-]*$ ]] || { echo "not repo:recipe:platform: $c"; exit 1; }; done
  if grep -q '^\[workers\]' config/config.toml; then echo "[workers] is already in config/config.toml: keeping it"; else
    CHECKS="${FACTORY_WORKER_CHECKS:-}" HOME_DIR="$HOME_DIR" python3 - <<'PY'
import os
out = '\n# Verification workers (docs/workers.md). Added by setup.sh: change them in the UI under Settings -> Workers.\n[workers]\nenabled = true\n'
out += 'listen = "127.0.0.1:8788"\ntokens_file = "%s/secrets/worker_tokens"\nmode = "block"\n' % os.environ["HOME_DIR"]
for c in os.environ["CHECKS"].split():
    repo, recipe, platform = c.split(":")
    out += '\n[[workers.checks]]\nrepo = "%s"\nrecipe = "%s"\nplatform = "%s"\n' % (repo, recipe, platform)
open("config/config.toml", "a").write(out)
PY
    echo "added [workers] to config/config.toml$([ -z "${FACTORY_WORKER_CHECKS:-}" ] && echo " (no checks yet: add them under Settings -> Workers)")"
  fi
fi

say "GitHub token"
put_secret() {  # name, value
  printf %s "$2" | $S tee "$HOME_DIR/secrets/$1" >/dev/null; $S chmod 600 "$HOME_DIR/secrets/$1"; [ "$(id -u)" = 1000 ] || $S chown 1000:1000 "$HOME_DIR/secrets/$1"; }
repo_list() { python3 - <<'PY' 2>/dev/null || true
import tomllib
c = tomllib.load(open("config/config.toml", "rb"))
r = list(c.get("github", {}).get("repos", []))
for p in c.get("projects", []):
    r += [x["repo"] if isinstance(x, dict) else x for x in p.get("repos", [])]
print(" ".join(dict.fromkeys(r)))
PY
}
check_github() {  # token -> prints problems, returns 1 when the token cannot do the job
  local t="$1" login code r bad=0
  login="$(curl -fsS -H "Authorization: Bearer $t" https://api.github.com/user 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["login"])' 2>/dev/null)" \
    || { echo "  x GitHub rejected this token (expired, mistyped, or not allowed)."; return 1; }
  echo "  ok  token works (account: $login)"
  for r in $(repo_list); do
    code="$(curl -s -H "Authorization: Bearer $t" "https://api.github.com/repos/$r" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("ok" if d.get("permissions",{}).get("push") else ("read-only" if d.get("id") else "missing"))' 2>/dev/null || echo missing)"
    case "$code" in
      ok) echo "  ok  $r (write access)";;
      read-only) echo "  x $r: the token can only read it. It needs Contents, Issues and Pull requests: read and write."; bad=1;;
      *) echo "  x $r: not found. Is the repo name right, and is it included in the token's repositories?"; bad=1;;
    esac
  done
  return $bad
}
if [ -s "$HOME_DIR/secrets/github_token" ]; then echo "github_token exists: keeping it"; else
  cat <<'GH'
The factory acts on GitHub as an account (a bot account is best, so PRs are not authored as you). Create a fine-grained token at
  https://github.com/settings/personal-access-tokens/new
  Repository access: only the repos you chose above.
  Permissions: Contents, Issues, Pull requests = Read and write; Metadata = Read; Actions = Read (for CI feedback).
GH
  T="${GITHUB_TOKEN:-}"; tries=0
  while :; do
    [ -n "$T" ] || { [ -n "$INTERACTIVE" ] && { read -rsp "Paste the token: " T; echo; } || true; }
    [ -n "$T" ] || { echo "a GitHub token is required (set GITHUB_TOKEN)"; exit 1; }
    if [ -n "${SKIP_CHECKS:-}" ] || check_github "$T"; then break; fi
    tries=$((tries+1)); T=""
    [ -n "$INTERACTIVE" ] && [ "$tries" -lt 3 ] || { echo "Fix the token and run ./scripts/setup.sh again (what is done so far is kept)."; exit 1; }
    echo "Try again."
  done
  put_secret github_token "$T"
fi

say "Model credentials for the agents"
if [ -s "$HOME_DIR/secrets/claude.env" ]; then echo "claude.env exists: keeping it"; else
  K="${ANTHROPIC_API_KEY:-}"; O="${CLAUDE_CODE_OAUTH_TOKEN:-}"
  if [ -z "$K$O" ]; then
    [ -n "$INTERACTIVE" ] || { echo "model credentials are required (set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN)"; exit 1; }
    cat <<'CL'
Choose how the agents sign in to Claude:
  1) An Anthropic API key (recommended for always-on use): create one at https://console.anthropic.com/settings/keys
  2) A Claude subscription token: run  claude setup-token  on any machine where you are signed in to Claude Code, and paste what it prints.
     (Anthropic's terms limit subscription use to ordinary individual use; an API key is the supported route for automation.)
CL
    c="$(ask "Which one? [1] " 1)"
    if [ "$c" = 2 ]; then
      if command -v claude >/dev/null 2>&1 && [ "$(ask "Run 'claude setup-token' now? [Y/n] " Y)" != n ]; then claude setup-token || true; fi
      read -rsp "Paste the token: " O; echo
    else read -rsp "Paste the API key: " K; echo; fi
  fi
  tries=0
  while [ -n "$K" ] && [ -z "${SKIP_CHECKS:-}" ]; do
    code="$(curl -s -o /dev/null -w '%{http_code}' -H "x-api-key: $K" -H "anthropic-version: 2023-06-01" https://api.anthropic.com/v1/models || echo 000)"
    [ "$code" = 200 ] && { echo "  ok  the API key works"; break; }
    echo "  x Anthropic answered $code for this key (401 means the key is wrong or revoked)."
    tries=$((tries+1)); K=""
    [ -n "$INTERACTIVE" ] && [ "$tries" -lt 3 ] || { echo "Fix the key and run ./scripts/setup.sh again."; exit 1; }
    read -rsp "Paste the API key again: " K; echo
  done
  if [ -n "$K" ]; then put_secret claude.env "ANTHROPIC_API_KEY=$K"
  elif [ -n "$O" ]; then put_secret claude.env "CLAUDE_CODE_OAUTH_TOKEN=$O"; echo "  (a subscription token cannot be checked here: 'ctl doctor' at the end only checks that it is present)"
  else echo "model credentials are required"; exit 1; fi
fi

if [ -z "${SKIP_BUILD:-}" ]; then
  if [ -f Dockerfile ]; then
    say "Building images (orchestrator, agent sandbox, design preview renderer, screen checker). This takes a few minutes."
    docker compose --profile build build
  else
    say "Fetching the release images"
    ./scripts/update.sh --images-only
  fi
fi

say "Password for the web UI"
PW="${FACTORY_UI_PASSWORD:-}"
if [ -z "$PW" ]; then
  [ -n "$INTERACTIVE" ] || { echo "set FACTORY_UI_PASSWORD (at least 10 characters)"; exit 1; }
  while :; do
    read -rsp "Choose a password (at least 10 characters): " PW; echo
    [ "${#PW}" -ge 10 ] || { echo "  too short."; continue; }
    read -rsp "Type it again: " PW2; echo
    [ "$PW" = "$PW2" ] && break; echo "  they differ."
  done
fi
FACTORY_UI_PASSWORD="$PW" docker compose run --rm -T -e FACTORY_UI_PASSWORD ui python3 -m factory.ui --config /etc/factory/config.toml --set-password

say "Where will you open the web UI?"
UI_ACCESS="${UI_ACCESS:-}"; UI_HOST="${FACTORY_UI_HOST:-}"
if [ -z "$UI_ACCESS" ]; then
  if [ -n "$INTERACTIVE" ]; then
    echo "  1) on this computer only (safest; use an SSH tunnel to reach it from elsewhere)"
    echo "  2) from other computers on my network (plain HTTP: only on a network you trust, or behind a VPN)"
    [ "$(ask "Which one? [1] " 1)" = 2 ] && UI_ACCESS=network || UI_ACCESS=local
  else UI_ACCESS=local; fi
fi
UI_URL="http://127.0.0.1:8787"
if [ "$UI_ACCESS" = network ]; then
  [ -n "$UI_HOST" ] || UI_HOST="$(ask "The name or address people will type (default: this machine's address) [$(hostname -I 2>/dev/null | awk '{print $1}')] " "$(hostname -I 2>/dev/null | awk '{print $1}')")"
  [ -n "$UI_HOST" ] || { echo "a host name or address is needed for network access"; exit 1; }
  for k in FACTORY_UI_BIND FACTORY_UI_ALLOWED_HOSTS; do sed -i "s|^#\? *$k=.*|$k=__$k|" .env; done
  sed -i "s|^FACTORY_UI_BIND=__.*|FACTORY_UI_BIND=0.0.0.0|; s|^FACTORY_UI_ALLOWED_HOSTS=__.*|FACTORY_UI_ALLOWED_HOSTS=$UI_HOST|" .env
  grep -q '^FACTORY_UI_BIND=' .env || printf 'FACTORY_UI_BIND=0.0.0.0\nFACTORY_UI_ALLOWED_HOSTS=%s\n' "$UI_HOST" >> .env
  UI_URL="http://$UI_HOST:8787"
fi

if [ -z "${SKIP_LABELS:-}" ]; then
  say "Labels in your repos"
  docker compose run --rm -e FACTORY_CONFIG=/etc/factory/config.toml orchestrator python3 -m factory.ctl labels || echo "(labels failed: fix the token, then run: docker compose run --rm orchestrator python3 -m factory.ctl labels)"
fi

if [ -n "${FACTORY_WORKERS:-}" ]; then
  say "Token for worker ${FACTORY_WORKER_NAME:-worker} (shown once)"
  docker compose run --rm -T -e FACTORY_CONFIG=/etc/factory/config.toml ui python3 -m factory.ctl workers add "${FACTORY_WORKER_NAME:-worker}" \
    || echo "(no new token: one with that name exists, or the command failed. Create another with: docker compose run --rm ui python3 -m factory.ctl workers add <name>)"
fi

if [ -z "${SKIP_START:-}" ]; then
  say "Starting"; docker compose up -d
  if [ -z "${SKIP_CHECKS:-}" ]; then
    printf 'Waiting for the web UI'; ok=""
    for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:8787/healthz" >/dev/null 2>&1 && { ok=1; break; }; printf .; sleep 2; done; echo
    [ -n "$ok" ] && echo "  ok  the UI is up" || echo "  x the UI did not answer yet: docker compose logs ui"
    say "Checking the install"
    docker compose run --rm -e FACTORY_CONFIG=/etc/factory/config.toml orchestrator python3 -m factory.ctl doctor || true
  fi
fi
cat <<MSG

Done. Shikumi is running in DRY-RUN: it only logs what it would do, and writes nothing to GitHub.

  Open the UI:   $UI_URL    (sign in with the password you just chose)
  When ready:    on the home screen, tick the box and press "Go live".
  Logs:          docker compose logs -f orchestrator
  Walkthrough:   docs/first-ticket.md   (open a small issue, put the label  factory:analyze  on it)
$([ "$UI_ACCESS" = local ] && printf '  From another computer: ssh -L 8787:127.0.0.1:8787 <this-host>, then open http://127.0.0.1:8787\n')$([ -n "${FACTORY_WORKERS:-}" ] && printf '  Workers: the worker API listens on 127.0.0.1:8788 (reach it from the worker machine with: ssh -L 8788:127.0.0.1:8788 <this-host>).\n  On that machine:  curl -fsSL https://github.com/theninjadojo/software-factory/releases/latest/download/install-worker.sh | bash\n  and paste the token above. Details: docs/workers.md\n')
MSG
