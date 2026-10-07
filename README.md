# Shikumi (仕組み)

*Shikumi* is Japanese for a mechanism, a way of building a system so that it works. Label a GitHub issue and sandboxed AI agents analyse it, design it, plan it, build it and open pull requests, with a
person kept in the loop on Telegram or Slack.

It is built around one assumption: **the agent will be prompt-injected by something it reads, so make that not matter.**
Agents run with no network, no credentials and no way to reach the host. All they can produce is data (a patch or a
document), and the orchestrator validates that data before it touches GitHub.

> **Documentation:** start at the [docs index](docs/README.md).
>
**[Project site](https://theninjadojo.github.io/software-factory/)**: the same story, with a getting-started walkthrough.

> **Status:** a working prototype, run for real on one multi-repo project. Read [Limits](#limits-and-honest-caveats) and
> [SECURITY.md](SECURITY.md) before pointing it at anything you care about.

## How it works

```
 GitHub issues, labeled by a person with write access
        │ poll
        ▼
 ┌──────────────┐  trust gate: who applied the label? (fails closed)
 │ orchestrator │  classifier (Jev or labels): kind, tier, effort, needs-a-person?, next stage
 └──────┬───────┘  a FIXED table maps the answer to a model and effort
        │ starts a sandbox with every repo of the project checked out side by side
        ▼
 ┌────────────────────────────────┐  unix socket   ┌───────────────┐
 │ sandbox: no network, no secrets │ ─────────────► │ egress proxy  │─► the model API host only
 │ read-only root, no capabilities │                └───────────────┘
 └───────────────┬────────────────┘
        a patch, or a document   (untrusted data)
                 ▼
 orchestrator validates it ─► applies it to pristine clones ─► pushes factory/* branches ─► opens PRs
                          └─► or posts a sanitized document on the ticket
                 ▼
 the repos' own CI is watched ─► result on the PR, the ticket and Telegram ─► optional agent fix round
```

### Labels (applied by a person; the factory acts only if they have write access)

| Label | What happens |
|---|---|
| `factory:analyze` | An **analyst** writes requirements, acceptance criteria and open questions on the ticket. Read-only. |
| `factory:design` | A **designer** writes the UX design (flows, states, copy, accessibility) on the ticket. In a repo with a `design/` folder of Claude Design canvases (`*.dc.html`) it also adds static mockups there on a **draft PR** and links them from the ticket. |
| `factory:architect` | An **architect** writes the technical plan (data model, API, cross-repo order, tests). Read-only. |
| `factory:ready` | **Build it.** Agents edit the repos and open one PR per changed repo. Earlier stage documents are given to the builder. |
| `factory:review` | A **code reviewer** reviews the ticket's open PRs and comments on each one (see below). Needs `[review] enabled = true`. |
| `factory:fix-conflicts` | **Resolve merge conflicts** in the ticket's open factory PRs: the base branch is merged into each PR branch and an agent resolves what git cannot (see below). The factory applies it itself when it finds a conflict. Needs `[conflicts] enabled = true`. |
| `factory:unblocked` | The factory ignores the **project manager's** blockers on this ticket (see below). |
| `factory:auto` | The classifier picks the next stage from the ticket and which stages are already done (`stage:*` labels) and runs it. It then **continues to the next stage by itself while nothing needs a person**, reading the document it just wrote (open questions make it stop and ask you on Telegram). Read-only stages never wait for approval. Stages are skipped when not needed: a small, clear ticket goes straight to a build. A high-complexity ticket (or a medium one spanning several repositories) gets the architect first, and a classification with no stage runs the analyst first (only on a ticket no stage has run on yet: auto never goes back to an earlier stage than one already done). An explicit stage or `factory:ready` label is always obeyed. The skipped stages are recorded in the admin UI. When the classifier is unsure (low confidence, or it says a person is needed) and no stage has run yet, it runs the analyst first instead of asking; a person is asked only after that, or before a build it is still unsure about. |

Other labels the factory manages: `factory:working[-role]`, `factory:pr-open`, `factory:failed`, `factory:needs-answers` (open questions wait for a person; cleared when they are answered or a later job starts), `factory:waiting-release` (a packages PR must be merged and published before the repos that use it are built), `stage:analysed`,
`stage:designed`, `stage:architected`.

### Features

- **Multi-repo projects.** Repos that work together (web, mobile, shared packages) are one workspace; one task can change
  several, producing cross-linked PRs that are all-or-nothing (if any repo's patch is rejected, nothing is pushed).
- **Packages before the apps that use them.** A repo can `publish` packages that another repo `depends_on` (`[[projects]]` in
  the config). A change to both is built in two waves: the packages PR first; after a person merges it and it is published (a
  GitHub release, a tag, or a publish workflow run that contains the merge), the factory builds the depending repo against the
  published version. The ticket shows `factory:waiting-release` meanwhile, and Telegram/Slack say when it was merged and published.
  A [worker](docs/workers.md#lockfiles-for-published-packages) can refresh the depending repo's lockfile for the new version, which
  the sandboxed agent cannot do.
  You don't have to write this by hand: **Settings → Projects → Build order → Find package dependencies** reads the repos'
  package.json files and workflows and suggests it (or `python3 -m factory.ctl deps [--apply]`), and the factory says so once on
  Telegram/Slack when it finds a link that is not set.
- **Role agents.** Analyst, designer, architect: their output lands on the ticket, and later stages build on it.
- **Classification.** [Jev](https://openrouter.ai/) (a typed "decision model" via OpenRouter) reads the whole ticket and
  picks tier, effort and stage as *typed answers*, never free text. Falls back to your labels if it errors.
- **Code review.** An independent reviewer agent reads the PR branches of every repo in the change, with full history, and posts a
  structured review (verdict, blocking / should-fix / nits with file and line, what is missing, what it could not verify) on each PR.
  It runs automatically after the factory opens a PR (which stays a draft until the review is posted), or when a person labels the ticket `factory:review`. It never approves, requests
  changes, merges or edits code. Give it a different harness and model family from the builder for a genuinely independent opinion.
  Off by default (it costs a run per PR).
- **Project manager.** On a periodic sweep an agent ranks each repository's factory tickets with `priority: high` / `priority: low`
  labels (the poller starts higher priority first) and records which tickets are blocked by others. While it is on, a build waits until
  its blockers are closed: the ones the project manager found, and any `Blocked by #N` line in the ticket's body. A priority label a
  person set always wins, `factory:unblocked` (applied by someone with write access) overrides its blockers, and it comments on a ticket
  only when it changed something. It never starts, stops or approves work. Off by default (`[pm] enabled`).
- **CI feedback.** Watches the repos' own CI on factory PRs, reports pass/fail, and can give the agent a fix round with
  the failing logs.
- **Merge conflicts.** Notices when a factory PR (build or design) conflicts with its base branch, reports it once on the PR, the ticket
  and Telegram, and applies `factory:fix-conflicts`. The orchestrator merges the base branch into the PR branch on its own clone; if git
  cannot finish, an agent edits only the conflicted files, every marker must be gone, and the merge commit is pushed. Never a rebase or a
  force-push; at most 10 attempts per PR. Off by default.
- **Telegram.** Alerts with a verbosity setting, `/status /usage /pause /resume`, and Run/Skip buttons for tickets that need a
  person. Only your Telegram user id is obeyed.
- **Slack.** The same, instead of or alongside Telegram: alerts, Run/Skip/answer buttons, and `/factory status|usage|pause|resume`.
  Socket Mode, so no public URL is needed. Only your Slack member id is obeyed.
- **Recovery.** If the orchestrator restarts mid-task, leftovers are cleared and the ticket is requeued. A run that crashes (for example GitHub timing out after the agent finished) marks the ticket `factory:failed` with a comment and an alert, and reads and label changes are retried when GitHub times out.
- **Admin UI.** See what is happening and what happened (runs, decisions, PRs and CI, an event timeline), and change settings,
  credentials, Telegram and agent harnesses without editing files. Authenticated, loopback by default. See [docs/ui.md](docs/ui.md).
- **Other agents.** A harness is `{image, credential, command, hosts}`. Claude Code is built in; Codex and Gemini ship as
  experimental templates.

## Quick start

### You will need

- A Linux host with **Docker and the compose plugin** (or Podman for the native install), plus `curl` and `python3`.
- A **GitHub account for the factory** (a bot account is best, so PRs aren't authored as you) with write access to your
  repos, and a **fine-grained token** for it: Contents, Issues and Pull requests *read and write*, Metadata *read*; add
  **Actions** *read* for the CI feedback (Commit statuses *read* is optional; the Checks permission is not needed). No Workflows, no Administration.
- **Model credentials** for the agent: `ANTHROPIC_API_KEY` (the supported route for unattended use) or a Claude
  subscription token from `claude setup-token` (see [caveats](#limits-and-honest-caveats)).
- Optional: a **Telegram bot** (BotFather) and your numeric Telegram id, or a **Slack app** (below); an **OpenRouter key** for Jev.

### Option 1: install from a release (recommended; no git, no build)

```bash
curl -fsSL https://github.com/theninjadojo/software-factory/releases/latest/download/install.sh | bash
```

This creates `./shikumi`, downloads the release files, pulls the prebuilt images (`ghcr.io/theninjadojo/shikumi*`) and runs
`scripts/setup.sh`, a guided setup that:

1. asks which repos to work on and writes the config;
2. walks you through the GitHub token (the exact permissions, with a link) and **checks it works and can write to each repo**;
3. helps you choose Claude credentials (an Anthropic API key, checked live, or a subscription token from `claude setup-token`);
4. asks you to choose and confirm a UI password;
5. asks whether you will open the UI on this machine or from other computers on your network;
6. creates the labels in your repos, starts everything in **dry-run**, waits for the UI, and runs a health check.

At the end it prints the URL. Nothing is written to GitHub until you press **Go live** on the UI's home screen.
It is non-interactive when you pass `GITHUB_TOKEN`, `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`),
`FACTORY_REPOS="org/a org/b"` and `FACTORY_UI_PASSWORD`.

This needs the repository's release files and the container packages (`ghcr.io/theninjadojo/shikumi*`, listed in [docs/releasing.md](docs/releasing.md)) to be
readable by you. If you run a private fork, sign in to GitHub and `ghcr.io` first, or use option 2.

### Option 2: from source (Docker)

```bash
git clone https://github.com/theninjadojo/software-factory && cd software-factory
./scripts/setup.sh      # the same setup, but builds the four images from source
```

### Then

```bash
docker compose run --rm -e FACTORY_CONFIG=/etc/factory/config.toml orchestrator python3 -m factory.ctl doctor   # checks the install
```

Open the UI at http://127.0.0.1:8787 (remote access: [docs/ui.md](docs/ui.md)) and follow [docs/first-ticket.md](docs/first-ticket.md).
When the dry-run log looks right, tick the box and press **Go live** on the home screen (it is one click back to dry run).

### Updating

| Installed from | Update with |
|---|---|
| a release | `./scripts/update.sh` (latest) or `./scripts/update.sh v0.2.0`. Refuses while an agent run is in flight, rolls back if the new version does not start. See [docs/releasing.md](docs/releasing.md). |
| source | `./scripts/update.sh` asks, then runs `git pull --ff-only` (with the factory's GitHub token) and `docker compose --profile build build`, then asks whether to restart on the new images (`docker compose up -d`; it warns if an agent run is in flight). Or by hand: `git pull && docker compose --profile build build && docker compose up -d` |
| native (Podman) | on the host: `sudo -n -u factory /srv/factory/app/deploy/update-native.sh` (a release, with tests, image rebuild and rollback); or `scripts/deploy.sh user@host [--image]` to push your checkout |
| a verification worker machine | `./scripts/update-worker.sh` (see [below](#verification-workers-optional)) |

The UI shows a banner when a newer release exists (`[updates] check = false` turns that off). `python3 -m factory.ctl version` prints what is running.

### Docker, step by step (what `setup.sh` does)

```bash
sudo mkdir -p /srv/factory/{secrets,state,work,run} && sudo chown -R 1000:1000 /srv/factory

mkdir config && cp config.example.toml config/config.toml   # edit: engine = "docker", your repos/projects
cp .env.example .env                   # set DOCKER_GID=$(stat -c %g /var/run/docker.sock)

# secrets: files, 0600, never in the repo (use `read -rs` so they stay out of shell history)
read -rsp "GitHub token: " T; printf %s "$T" > /srv/factory/secrets/github_token; echo
printf 'ANTHROPIC_API_KEY=%s\n' "$KEY" > /srv/factory/secrets/claude.env
chmod 600 /srv/factory/secrets/*

docker compose --profile build build   # four images: orchestrator, agent sandbox, design-preview renderer, screen checker
docker compose run --rm ui python3 -m factory.ui --config /etc/factory/config.toml --set-password   # choose the UI password
docker compose run --rm -e FACTORY_CONFIG=/etc/factory/config.toml orchestrator python3 -m factory.ctl labels   # create the labels
docker compose up -d                   # starts in dry-run: it logs decisions and acts on nothing
docker compose logs -f orchestrator
```

**Read the security note in `docker-compose.yml`:** on a rootful Docker the engine socket the orchestrator uses is
root-equivalent on the host. Prefer rootless Docker, or the native install below.

### Native (rootless Podman + systemd)

The hardened path: no engine socket is shared with a container. See [docs/operations.md](docs/operations.md) for the
full layout, the unit files in `deploy/systemd/`, and `scripts/deploy.sh` for deploying over SSH.

### First run

The step-by-step version, with what you should see at each step, is [docs/first-ticket.md](docs/first-ticket.md).

1. The labels (`factory:ready`, `factory:analyze`, `factory:design`, `factory:architect`, `factory:auto` and the rest) are created by `setup.sh`; to add them to another repo, run `python3 -m factory.ctl labels owner/repo`.
2. Open a small, low-risk issue and apply `factory:analyze`. A document should appear on the ticket.
3. Apply `factory:ready` to build it. A PR should appear within a few minutes.

### Verification workers (optional)

The agent sandbox has no network and runs on Linux, so agents cannot install dependencies, run a build, or touch Xcode. A **verification
worker** is a machine you own (a Mac, or a Linux box) that checks an agent's patch *before anything is pushed*: it clones the repo, applies the
patch, runs a recipe that **lives on the worker**, and sends back pass/fail, the log and screenshots. A failing check fails the run (or, set to
`warn`, is noted on the PR), and a real test failure gets an agent fix round with the log. How it works, the security model and the plan:
[docs/workers.md](docs/workers.md).

It installs the same way as the factory, in two steps.

**1. On the factory host**, turn the worker API on and make a token (add `FACTORY_WORKERS=1` to the setup, or to an existing install run the
second command):

```bash
FACTORY_WORKERS=1 FACTORY_WORKER_NAME=my-mac FACTORY_WORKER_CHECKS="your-org/app:web-test:any your-org/ios:ios-test:macos" ./scripts/setup.sh
docker compose run --rm -e FACTORY_CONFIG=/etc/factory/config.toml ui python3 -m factory.ctl workers add my-mac   # another token later
```

This adds `[workers]` to `config/config.toml` and prints the token once. The UI starts the worker API by itself whenever workers are enabled
(turning them on under **Settings → Workers** is enough). The API listens on `127.0.0.1:8788`: reach it from the worker with an SSH tunnel (`ssh -L 8788:127.0.0.1:8788 host`), a
VPN or a TLS proxy. Checks can also be edited in the UI under **Settings → Workers**; the **Workers** page shows who is online and every job's log.
Native installs: enable workers in the UI or `config.toml`, and add tokens with `python3 -m factory.ctl workers add`.

**2. On the worker machine** (use a dedicated unprivileged account: a recipe runs agent-written code):

```bash
curl -fsSL https://github.com/theninjadojo/software-factory/releases/latest/download/install-worker.sh | bash
```

This creates `./shikumi-worker` and runs `scripts/setup-worker.sh`, which asks for the factory URL and the token, writes `worker.toml` and the token
file (mode 0600), installs a launchd (macOS) or systemd user service, and finishes with a check (`worker.py --check`) that proves the URL, the token,
git and every recipe. It is non-interactive when you pass `FACTORY_URL`, `WORKER_TOKEN` and optionally `WORKER_RECIPES="web android"` (and, for iOS, `WORKER_IOS_SCHEME` plus a project or workspace).
You also need Python 3.11+ and git on the worker, and git credentials that can **clone** your repos (read-only).

| Recipe | Does | Needs on the worker |
|---|---|---|
| `web-test` | `npm ci` / `pnpm` / `yarn`, build, test, Playwright | Node |
| `android-test` | `./gradlew` in a throwaway container (`--emulator` on Linux with KVM) | Docker or Podman (the image is pulled, or built once from `sandbox/android`) |
| `ios-test` | `xcodebuild test` on a simulator inside a throwaway macOS VM (stub-tested only so far) | A Mac (Apple Silicon), [Tart](https://tart.run), and an Xcode VM image (about 30 GB, one-time); set `WORKER_IOS_SCHEME` and `WORKER_IOS_PROJECT` or `WORKER_IOS_WORKSPACE` |

Update with `./scripts/update-worker.sh` (refuses while a job runs, rolls back if the new version fails its check). `worker.toml`, the token and your
git setup are never touched. Check it any time: `python3 worker/worker.py --config worker.toml --check`, and on the factory `python3 -m factory.ctl doctor`.

### Telegram

Create a bot with BotFather, put its token in `secrets/telegram_token`, message the bot once, then read your id from
`https://api.telegram.org/bot<TOKEN>/getUpdates` (`message.from.id`) and set `chat_id` in `[telegram]`. Choose
`verbosity` = `quiet` | `normal` | `verbose`, or list `events` explicitly (see `config.example.toml`).

### Slack

Slack works instead of Telegram or alongside it. It uses **Socket Mode**: the factory opens an outbound connection to Slack, so
nothing needs to be reachable from the internet.

Everything is done from the UI's **Slack** page (Settings → Slack), which walks through it:

1. **Create the app in Slack** opens Slack with the app already described (a bot that may post and owns `/factory`, buttons, Socket
   Mode). Pick your workspace, confirm and **Install to Workspace**. The manifest is also shown on the page, to paste by hand.
2. Paste the *Bot User OAuth Token* (`xoxb-...`) and an app-level token with the `connections:write` scope (`xapp-...`, under
   **Basic Information → App-Level Tokens**). Each has a Test button. The factory connects to Slack when it is next idle.
3. In the channel for alerts (or a DM with the app) run `/invite @Shikumi`, then `/factory`. You appear on the Slack page with your
   member id and the channel; press **Use this**. Until then nobody is obeyed and no alert is sent.
4. Choose how chatty it is, optionally the address of the UI (for an *Open in UI* button), and press *Send a test message*.

By hand instead: put the tokens in `secrets/slack_bot_token` and `secrets/slack_app_token` (mode 0600) and set `channel` and `user_id`
in `[slack]` (see `config.example.toml`).

Only the member id you configure is obeyed (button clicks must also come from the configured channel). Someone else who clicks a button or
runs `/factory` is ignored and listed on the Slack page, by id and display name only, so you can check your own id.

## Configuration

Everything is in one TOML file (plus the optional `config.overrides.toml` the UI writes); `config.example.toml` documents every key. The parts you will touch first:
`[github]` (repos, trigger label), `[[projects]]` (repos that work together, with a one-line role for each),
`[routing.*]` (tier → model and effort), `[runner]` (engine, image, allowed hosts), `[classifier]`, `[telegram]`, `[slack]`, `[ci]`.

## Limits and honest caveats

- **Agents cannot build or run tests** in the sandbox: it has no network, so they cannot install dependencies. Their changes are
  reasoned, not executed; your repos' CI is the real check (hence the CI feedback loop). Optional [verification workers](docs/workers.md)
  (a Mac, or any machine you own) can build and test a patch before it is pushed.
- **Subscription auth is a gray area.** Anthropic's docs describe `claude setup-token` for scripts and CI, but their terms
  limit subscription use to "ordinary, individual usage". Always-on automation is arguably outside that; an API key is the
  supported route. Decide for yourself.
- **Protect `main` on GitHub.** The factory only pushes `factory/*` branches (enforced in code and tests), but branch
  protection or rulesets requiring review are the real control.
- **A hijacked agent can still write bad code or misleading documents.** Review every PR. See SECURITY.md.
- **Jev's Decisions API is an alpha endpoint** and may change; the classifier degrades to labels on any error.
- One orchestrator; it runs up to `[runner] max_parallel` tasks at once (default 1).

## Roadmap

- Verification workers: the mechanism, the admin page, fix rounds, the web recipe and the Android recipe exist and are installable (above).
  The iOS recipe (Xcode in a throwaway Tart VM) is written too. Still to do: run the iOS recipe and the Android emulator path on real hardware (the
  Android unit-test path has been run for real; iOS and the emulator are stub-tested only), and run a worker end to end on a Mac.
  See [docs/workers.md](docs/workers.md).
- Verifying the Codex and Gemini harness templates end to end (the mechanism is tested; their command lines are not yet).

## Development

```bash
python3 -m unittest discover -s tests     # standard library only; no network, no containers
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/architecture.md](docs/architecture.md).

## License

MIT, see [LICENSE](LICENSE).
