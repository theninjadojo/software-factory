#!/usr/bin/env bash
# One-command first-time setup for the Docker install: directories, config, secrets, images, UI password, labels.
#   ./scripts/setup.sh                      # asks for what it needs
#   GITHUB_TOKEN=... ANTHROPIC_API_KEY=... FACTORY_REPOS="org/a org/b" ./scripts/setup.sh   # non-interactive (no prompts)
# Optional environment: FACTORY_HOME (default /srv/factory), ENGINE (docker|podman, default: what is installed),
#   SKIP_BUILD=1, SKIP_START=1, SKIP_LABELS=1, FACTORY_UI_PASSWORD (skips the password prompt).
# Verification workers (optional, docs/workers.md): FACTORY_WORKERS=1 turns on the worker API, and creates a token for FACTORY_WORKER_NAME
#   (default "worker"). FACTORY_WORKER_CHECKS="org/app:web-test:any org/ios:ios-test:macos" adds [[workers.checks]] (repo:recipe:platform).
# It starts the factory in DRY-RUN: it logs what it would do and touches nothing until you turn dry_run off.
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

say "Directories under $HOME_DIR"
# sudo only when this user cannot write there (the containers run as uid 1000, so that user must own the folders)
S=""; mkdir -p "$HOME_DIR" 2>/dev/null && [ -w "$HOME_DIR" ] || S=sudo
$S mkdir -p "$HOME_DIR"/{secrets,state,work,run}
[ "$(id -u)" = 1000 ] || $S chown -R 1000:1000 "$HOME_DIR"
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
  # the worker API is a compose profile: with COMPOSE_PROFILES set, plain `docker compose up -d` (and update.sh) include it
  if grep -q '^COMPOSE_PROFILES=' .env; then grep -q '^COMPOSE_PROFILES=.*workers' .env || sed -i 's|^COMPOSE_PROFILES=\(.*\)$|COMPOSE_PROFILES=\1,workers|' .env
  else printf 'COMPOSE_PROFILES=workers\n' >> .env; fi
fi

say "Secrets (files, mode 0600, outside the repo)"
put_secret() {  # name, value
  printf %s "$2" | $S tee "$HOME_DIR/secrets/$1" >/dev/null; $S chmod 600 "$HOME_DIR/secrets/$1"; [ "$(id -u)" = 1000 ] || $S chown 1000:1000 "$HOME_DIR/secrets/$1"; }
if [ -s "$HOME_DIR/secrets/github_token" ]; then echo "github_token exists: keeping it"; else
  T="${GITHUB_TOKEN:-}"; [ -n "$T" ] || { read -rsp "GitHub token (fine-grained: Contents, Issues, Pull requests read+write; Actions read): " T; echo; }
  [ -n "$T" ] || { echo "a GitHub token is required"; exit 1; }; put_secret github_token "$T"; fi
if [ -s "$HOME_DIR/secrets/claude.env" ]; then echo "claude.env exists: keeping it"; else
  K="${ANTHROPIC_API_KEY:-}"; [ -n "$K" ] || { read -rsp "ANTHROPIC_API_KEY (or press Enter to paste a CLAUDE_CODE_OAUTH_TOKEN): " K; echo; }
  if [ -n "$K" ]; then put_secret claude.env "ANTHROPIC_API_KEY=$K"; else
    read -rsp "CLAUDE_CODE_OAUTH_TOKEN (from: claude setup-token): " K; echo; [ -n "$K" ] || { echo "model credentials are required"; exit 1; }
    put_secret claude.env "CLAUDE_CODE_OAUTH_TOKEN=$K"; fi; fi

if [ -z "${SKIP_BUILD:-}" ]; then
  if [ -f Dockerfile ]; then
    say "Building images (orchestrator, agent sandbox, design preview renderer, screen checker). This takes a few minutes."
    docker compose --profile build build
  else
    say "Fetching the release images"
    ./scripts/update.sh --images-only
  fi
fi

say "UI password"
if [ -n "${FACTORY_UI_PASSWORD:-}" ]; then
  docker compose run --rm -T -e FACTORY_UI_PASSWORD ui python3 -m factory.ui --config /etc/factory/config.toml --set-password
else docker compose run --rm ui python3 -m factory.ui --config /etc/factory/config.toml --set-password; fi

if [ -z "${SKIP_LABELS:-}" ]; then
  say "Labels in your repos"
  docker compose run --rm -e FACTORY_CONFIG=/etc/factory/config.toml orchestrator python3 -m factory.ctl labels || echo "(labels failed: fix the token, then run: docker compose run --rm orchestrator python3 -m factory.ctl labels)"
fi

if [ -n "${FACTORY_WORKERS:-}" ]; then
  say "Token for worker ${FACTORY_WORKER_NAME:-worker} (shown once)"
  docker compose run --rm -T -e FACTORY_CONFIG=/etc/factory/config.toml ui python3 -m factory.ctl workers add "${FACTORY_WORKER_NAME:-worker}" \
    || echo "(no new token: one with that name exists, or the command failed. Create another with: docker compose run --rm ui python3 -m factory.ctl workers add <name>)"
fi

if [ -z "${SKIP_START:-}" ]; then say "Starting"; docker compose up -d; fi
cat <<MSG

Done. The factory is in DRY-RUN: it only logs what it would do.
  UI:    http://127.0.0.1:8787   (remote: ssh -L 8787:127.0.0.1:8787 <host>)
  Logs:  docker compose logs -f orchestrator
Check the install:  docker compose run --rm -e FACTORY_CONFIG=/etc/factory/config.toml orchestrator python3 -m factory.ctl doctor
Walkthrough: docs/first-ticket.md
$([ -n "${FACTORY_WORKERS:-}" ] && printf 'Workers: the worker API listens on 127.0.0.1:8788 (reach it from the worker machine with: ssh -L 8788:127.0.0.1:8788 <this-host>).\n  On that machine:  curl -fsSL https://github.com/theninjadojo/software-factory/releases/latest/download/install-worker.sh | bash\n  and paste the token above. Details: docs/workers.md\n')
Next: open a small issue, put the label  factory:analyze  on it, and watch the log. When the decisions look right, switch
dry_run off in the UI (Settings) or in config/config.toml, then  docker compose up -d.
MSG
