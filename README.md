# software-factory

Label a GitHub issue and sandboxed AI agents analyse it, design it, plan it, build it and open pull requests, with a
person kept in the loop on Telegram.

It is built around one assumption: **the agent will be prompt-injected by something it reads, so make that not matter.**
Agents run with no network, no credentials and no way to reach the host. All they can produce is data (a patch or a
document), and the orchestrator validates that data before it touches GitHub.

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
| `factory:auto` | The classifier picks the next stage from the ticket and which stages are already done (`stage:*` labels) and runs it. It then **continues to the next stage by itself while nothing needs a person**, reading the document it just wrote (open questions make it stop and ask you on Telegram). Read-only stages never wait for approval. When the classifier is unsure (low confidence, or it says a person is needed) and no analysis exists yet, it runs the analyst first instead of asking; a person is asked only after that, or before a build it is still unsure about. |

Other labels the factory manages: `factory:working[-role]`, `factory:pr-open`, `factory:failed`, `stage:analysed`,
`stage:designed`, `stage:architected`.

### Features

- **Multi-repo projects.** Repos that work together (web, mobile, shared packages) are one workspace; one task can change
  several, producing cross-linked PRs that are all-or-nothing (if any repo's patch is rejected, nothing is pushed).
- **Role agents.** Analyst, designer, architect: their output lands on the ticket, and later stages build on it.
- **Classification.** [Jev](https://openrouter.ai/) (a typed "decision model" via OpenRouter) reads the whole ticket and
  picks tier, effort and stage as *typed answers*, never free text. Falls back to your labels if it errors.
- **Code review.** An independent reviewer agent reads the PR branches of every repo in the change, with full history, and posts a
  structured review (verdict, blocking / should-fix / nits with file and line, what is missing, what it could not verify) on each PR.
  It runs automatically after the factory opens a PR, or when a person labels the ticket `factory:review`. It never approves, requests
  changes, merges or edits code. Give it a different harness and model family from the builder for a genuinely independent opinion.
  Off by default (it costs a run per PR).
- **CI feedback.** Watches the repos' own CI on factory PRs, reports pass/fail, and can give the agent a fix round with
  the failing logs.
- **Merge conflicts.** Notices when a factory PR (build or design) conflicts with its base branch, reports it once on the PR, the ticket
  and Telegram, and applies `factory:fix-conflicts`. The orchestrator merges the base branch into the PR branch on its own clone; if git
  cannot finish, an agent edits only the conflicted files, every marker must be gone, and the merge commit is pushed. Never a rebase or a
  force-push; at most 10 attempts per PR. Off by default.
- **Telegram.** Alerts with a verbosity setting, `/status /pause /resume`, and Run/Skip buttons for tickets that need a
  person. Only your Telegram user id is obeyed.
- **Recovery.** If the orchestrator restarts mid-task, leftovers are cleared and the ticket is requeued.
- **Admin UI.** See what is happening and what happened (runs, decisions, PRs and CI, an event timeline), and change settings,
  credentials, Telegram and agent harnesses without editing files. Authenticated, loopback by default. See [docs/ui.md](docs/ui.md).
- **Other agents.** A harness is `{image, credential, command, hosts}`. Claude Code is built in; Codex and Gemini ship as
  experimental templates.

## Quick start

### You will need

- A **GitHub account for the factory** (a bot account is best, so PRs aren't authored as you) with write access to your
  repos, and a **fine-grained token** for it: Contents, Issues and Pull requests *read and write*, Metadata *read*; add
  **Actions** *read* for the CI feedback (Commit statuses *read* is optional; the Checks permission is not needed). No Workflows, no Administration.
- **Model credentials** for the agent: `ANTHROPIC_API_KEY` (the supported route for unattended use) or a Claude
  subscription token from `claude setup-token` (see [caveats](#limits-and-honest-caveats)).
- Optional: a **Telegram bot** (BotFather) and your numeric Telegram id; an **OpenRouter key** for Jev.
- A container engine: **Podman** (recommended) or **Docker**.

### Docker

```bash
git clone <this repo> && cd software-factory
sudo mkdir -p /srv/factory/{secrets,state,work,run} && sudo chown -R 1000:1000 /srv/factory

mkdir config && cp config.example.toml config/config.toml   # edit: engine = "docker", image = "factory-agent:latest", your repos/projects
cp .env.example .env                   # set DOCKER_GID=$(stat -c %g /var/run/docker.sock)

# secrets: files, 0600, never in the repo (use `read -rs` so they stay out of shell history)
read -rsp "GitHub token: " T; printf %s "$T" > /srv/factory/secrets/github_token; echo
printf 'ANTHROPIC_API_KEY=%s\n' "$KEY" > /srv/factory/secrets/claude.env
chmod 600 /srv/factory/secrets/*

docker compose --profile build build   # the orchestrator image and the agent sandbox image
docker compose run --rm ui python3 -m factory.ui --config /etc/factory/config.toml --set-password   # choose the UI password
docker compose up -d                   # starts in dry-run: it logs decisions and acts on nothing
# the UI is now at http://127.0.0.1:8787 (see docs/ui.md for remote access)
docker compose logs -f orchestrator
```

When the dry run looks right, go live from the UI (or set `dry_run = false` in `config/config.toml`) and `docker compose up -d` again.
**Read the security note in `docker-compose.yml`:** on a rootful Docker the engine socket the orchestrator uses is
root-equivalent on the host. Prefer rootless Docker, or the native install below.

### Native (rootless Podman + systemd)

The hardened path: no engine socket is shared with a container. See [docs/operations.md](docs/operations.md) for the
full layout, the unit files in `deploy/systemd/`, and `scripts/deploy.sh` for deploying over SSH.

### First run

1. Create the labels in each repo (`factory:ready`, `factory:analyze`, `factory:design`, `factory:architect`, `factory:auto`).
2. Open a small, low-risk issue and apply `factory:analyze`. A document should appear on the ticket.
3. Apply `factory:ready` to build it. A PR should appear within a few minutes.

### Telegram

Create a bot with BotFather, put its token in `secrets/telegram_token`, message the bot once, then read your id from
`https://api.telegram.org/bot<TOKEN>/getUpdates` (`message.from.id`) and set `chat_id` in `[telegram]`. Choose
`verbosity` = `quiet` | `normal` | `verbose`, or list `events` explicitly (see `config.example.toml`).

## Configuration

Everything is in one TOML file (plus the optional `config.overrides.toml` the UI writes); `config.example.toml` documents every key. The parts you will touch first:
`[github]` (repos, trigger label), `[[projects]]` (repos that work together, with a one-line role for each),
`[routing.*]` (tier → model and effort), `[runner]` (engine, image, allowed hosts), `[classifier]`, `[telegram]`, `[ci]`.

## Limits and honest caveats

- **Agents cannot build or run tests.** The sandbox has no network, so they cannot install dependencies. Their changes are
  reasoned, not executed; your repos' CI is the real check (hence the CI feedback loop).
- **Subscription auth is a gray area.** Anthropic's docs describe `claude setup-token` for scripts and CI, but their terms
  limit subscription use to "ordinary, individual usage". Always-on automation is arguably outside that; an API key is the
  supported route. Decide for yourself.
- **Protect `main` on GitHub.** The factory only pushes `factory/*` branches (enforced in code and tests), but branch
  protection or rulesets requiring review are the real control.
- **A hijacked agent can still write bad code or misleading documents.** Review every PR. See SECURITY.md.
- **Jev's Decisions API is an alpha endpoint** and may change; the classifier degrades to labels on any error.
- One orchestrator, one task at a time.

## Roadmap

- A verification worker for a Mac (or any machine): pull-based, runs only recipes defined on the worker itself, to build
  and test web, Android and iOS and return logs and screenshots.
- Verifying the Codex and Gemini harness templates end to end (the mechanism is tested; their command lines are not yet).

## Development

```bash
python3 -m unittest discover -s tests     # standard library only; no network, no containers
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/architecture.md](docs/architecture.md).

## License

MIT, see [LICENSE](LICENSE).
