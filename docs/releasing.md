# Releases: publishing, installing, updating

## Cutting a release (maintainer)

1. Set `VERSION` to the new number (for example `0.2.0`) and merge it to `main`.
2. Tag and push: `git tag v0.2.0 && git push origin v0.2.0`.
3. The `release` workflow (`.github/workflows/release.yml`) checks the tag matches `VERSION`, runs the tests, builds and pushes four images to
   `ghcr.io/<owner>/shikumi`, `shikumi-agent`, `shikumi-render` and `shikumi-screens` (tagged `v0.2.0` and `latest`), and creates a GitHub
   Release with `docker-compose.yml`, `config.example.toml`, `VERSION`, `.env.example`, `setup.sh`, `update.sh` and `install.sh`.
4. First release only: GitHub makes new container packages private. In the repo's Packages settings, make the four packages public (or
   every installer needs a registry login).
5. Write what changed in the release notes, and say clearly when a config change is needed.

Use semantic versions: a patch for fixes, a minor for features that need no action, a major when an install must change its config or data.

## Installing from a release (user)

```bash
curl -fsSL https://github.com/theninjadojo/software-factory/releases/latest/download/install.sh | bash
```

It creates `./shikumi`, downloads the release files and runs `scripts/setup.sh` (repos, keys, UI password, labels, dry-run start). No git and no
build: the images are pulled. Needs Docker with the compose plugin, curl and python3.

## Updating

```bash
cd shikumi && ./scripts/update.sh          # the latest release
./scripts/update.sh v0.2.0                 # a specific one, also how you go back
```

It refuses to run while an agent run is in flight. It downloads the release files, pulls the new images, restarts, waits for the UI health check and
the orchestrator, and **rolls back by itself** (previous files, `.env` and sandbox images) if the new version does not come up. Your `config/`,
secrets, database and `.env` settings are never overwritten. The UI shows a banner when a newer release exists (`[updates] check = false` turns
the check off; it is one cached read of the public releases API every few hours).

`ctl version` and the UI footer show the running version.

## What a release carries (and what it needs from you)

The orchestrator image holds all of `factory/` (the UI and scheduled jobs included), so new modules ship without any packaging change;
`config.example.toml` and `docs/` describe them. New database tables are created on start (`CREATE TABLE IF NOT EXISTS`), and new config sections
are optional, so a **minor** release needs no action. Scheduled jobs (`[[schedules]]`, Settings → Schedules, `docs/schedules.md`) are an example:
nothing runs until a schedule is added, the orchestrator reads its keys from the mounted `secrets` directory and writes snapshots under `state/`, and
the UI container saves keys and queues "Run now" through the same two mounts.

## Not covered

- **Source checkouts** update with `git pull` and `docker compose --profile build build`.
- **Native Podman installs** use `scripts/deploy.sh user@host` (it tars the checkout, including `VERSION`).
- **Config or database changes** between versions are not migrated automatically: read the release notes before a major update.
