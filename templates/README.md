# Project templates

A new project's first commit is rendered from these files (`factory/scaffold.py`). A person picks a pattern and a deploy target in
the New project interview (`factory/newproject.py`). Each repository of the pattern is one **kind**, and the deploy target adds
an **overlay** on top of it.

```
templates/
  kinds/<kind>/                 a complete, runnable project: code, tests, CI, CLAUDE.md, README.md, lockfile
  deploy/<overlay>/<kind>/      added on top for one deploy target: the deploy workflow, platform config, DEPLOY.md
  verify/<kind>.sh              how CI checks the kind still installs, lints, type-checks, tests and builds
```

| Kind | Used by | Overlays |
|---|---|---|
| `nextjs` | web-app | vercel, fly, docker-vm, render |
| `vite-react` | spa-api (web) | cloudflare, docker-vm, render |
| `fastapi` | spa-api (api), api-service, mobile-app (api) | fly, docker-vm, render |
| `astro` | content-site | cloudflare, vercel, github-pages |
| `expo` | mobile-app (app) | eas |
| `python-package` | python-package | pypi |

## Placeholders

A template is a working project named `shikumi-app`, so it can be installed and tested right here. Rendering replaces, in every
file's text and in file names:

| Written in the template | Becomes |
|---|---|
| `shikumi-app` | the repository name, for example `team-rota-api` |
| `shikumi_app` | the same with underscores (a Python package name) |
| `SHIKUMI_APP` | the same in capitals (an environment variable prefix) |
| `Shikumi App` | the project's title |
| `shikumi-org` | the GitHub owner (for image names such as `ghcr.io/shikumi-org/shikumi-app`) |
| `<!-- shikumi:summary -->` | the plan's summary (in README.md) |

Do not use these strings, or the bare word, for anything else. Nothing else is templated: no conditionals, no loops.

## What every kind has

- **Code** laid out as the pattern's practices say (see the catalogue in `factory/newproject.py`). A small example feature runs end to end through every layer, with tests,
  so the first real feature has something to copy. Keep it small: a starting point, not a demo app.
- **Tests** that pass: units, plus end to end where the pattern says so. The tests need nothing outside the repository except
  what CI starts (for example a Postgres service container).
- **`.github/workflows/ci.yml`**, named `CI`: on pull requests and pushes to `main`. It runs install (from the lockfile), lint,
  type-check, tests and build. Use the current major versions of the actions.
- **`.github/dependabot.yml`**: weekly updates for the package ecosystem and for GitHub Actions, grouped so minor and patch
  updates arrive as one pull request.
- **A lockfile**, committed (`package-lock.json` or `uv.lock`). Never `node_modules`, `.venv` or build output.
- **`CLAUDE.md`**: how the code is organised, the conventions to follow, and the exact commands to install, run, test and lint.
  The factory's agents read it before every change. They have no network, so say plainly which commands need one.
- **`README.md`**: what it is (the `<!-- shikumi:summary -->` line), how to run it locally, how to test it, where it deploys
  (point at `DEPLOY.md`).
- **`.env.example`** when it needs configuration. Never a real secret.

## What every overlay has

- **`.github/workflows/deploy.yml`**, named `Deploy`. It runs after `CI` succeeds on `main` (`workflow_run`) and on
  `workflow_dispatch`. When a secret it needs is missing it **skips with a notice** (`::notice::`) that names the secret and
  points at `DEPLOY.md`, and the workflow still succeeds. A new repository must not show red runs before anyone has set up hosting.
- **The platform's config** (`fly.toml`, `render.yaml`, `compose.yaml`, `wrangler.toml`, `eas.json`, `vercel.json`, ...), only as much as
  the platform needs.
- **`DEPLOY.md`**: a step-by-step guide to set up the platform once (accounts, which secrets and variables to add in GitHub and where to
  find them, the first deploy, a custom domain), and how to roll back.

An overlay may replace a file of its kind only when it must (for example a `Dockerfile`). Otherwise it adds files.

## Checking a kind

`templates/verify/<kind>.sh` runs the kind's checks in place, as CI does. The `Templates` workflow of this repository runs every
kind weekly and whenever `templates/` changes. Keep the kinds green: a new project whose first CI run fails is a broken promise.
