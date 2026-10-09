# Releases: publishing, installing, updating

## Cutting a release (maintainer)

Releases are automatic. To release:

1. Change `VERSION` to the new number (for example `0.3.0`) in a PR and merge it to `main`. It must be higher than every existing tag
   (the workflow refuses otherwise).
2. That is all. The `release` workflow (`.github/workflows/release.yml`) runs on the merge: it runs the whole test workflow (unit, browser and image tests), and only if that passes it builds and pushes five images to
   `ghcr.io/<owner>/shikumi`, `shikumi-agent`, `shikumi-render`, `shikumi-screens` and `shikumi-android` (tagged `v0.3.0` and `latest`), creates
   the tag `v0.3.0` at that commit, and publishes a GitHub Release with `docker-compose.yml`, `config.example.toml`, `VERSION`, `env.example`
   (the `.env` template), `setup.sh`, `update.sh`, `install.sh`, `update-native.sh` and the worker files.
3. On every other merge to `main` the same workflow exits within seconds: the tag for that `VERSION` already exists.

From the UI: **Settings > Updates and releases** opens a `release/vX.Y.Z` pull request that bumps `VERSION` (patch, minor or major above the highest of `main` and the latest release), and **Merge and release** merges it, which starts the build. It needs a GitHub token that can write to `[updates] repo`. A pull request whose checks passed also has a **Merge** button on its ticket page.

Pushing a tag by hand (`git tag v0.3.0 && git push origin v0.3.0`) still works and runs the same release; the tag must match `VERSION`.
If a release run fails before it publishes, fix the cause and re-run the workflow (nothing is tagged until the release is created).

First release only: GitHub makes new container packages private. In the repo's Packages settings, make the packages public (or every installer
needs a registry login). Write what changed in the release notes (they are generated from the merged PRs), and say clearly when a config
change is needed.

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

It pauses the factory for the update and refuses to run while an agent run is in flight. It downloads the release files, pulls the new images, restarts, waits for the UI health check and
the orchestrator, and **rolls back by itself** (previous files, `.env` and sandbox images) if the new version does not come up. Your `config/`,
secrets, database and `.env` settings are never overwritten. The UI shows a banner when a newer release exists (`[updates] check = false` turns
the check off; it is one cached read of the public releases API every few hours).

`ctl version` and the UI footer show the running version.

## Verification workers: install and update (optional)

A worker is a separate machine (a Mac, or a Linux box with the tools a build needs), installed the same way as the factory, from the same release:

```bash
curl -fsSL https://github.com/theninjadojo/software-factory/releases/latest/download/install-worker.sh | bash
```

It creates `./shikumi-worker`, downloads `shikumi-worker.tar.gz` and runs `scripts/setup-worker.sh` (factory URL, token, recipes, the Android image,
a launchd or systemd user service, and a self-check). It is non-interactive when you pass `FACTORY_URL`, `WORKER_TOKEN` and `WORKER_RECIPES`.
Update with `./scripts/update-worker.sh [vX.Y.Z]`: it refuses while a job is running, replaces `worker/` and `sandbox/`, pulls the matching
`shikumi-android` image, restarts, runs `worker.py --check`, and **rolls back by itself** if that fails. `worker.toml`, `secrets/` and `work/` are never
touched. On the factory host, `FACTORY_WORKERS=1 ./scripts/setup.sh` turns workers on and makes a token; the UI then runs the worker API
itself (`update.sh` removes the separate `workers` container older releases used). See [workers.md](workers.md).

## What a release carries (and what it needs from you)

The orchestrator image holds all of `factory/` (the UI, scheduled jobs and worker API included), so new modules ship without any packaging change;
`config.example.toml` and `docs/` describe them. New database tables are created on start (`CREATE TABLE IF NOT EXISTS`), and new config sections
are optional, so a **minor** release needs no action. Scheduled jobs (`[[schedules]]`, Settings → Schedules, `docs/schedules.md`) are an example:
nothing runs until a schedule is added, the orchestrator reads its keys from the mounted `secrets` directory and writes snapshots under `state/`, and
the UI container saves keys and queues "Run now" through the same two mounts.

## Native installs (Podman + systemd)

```bash
sudo -n -u factory /srv/factory/app/deploy/update-native.sh            # the latest release
sudo -n -u factory /srv/factory/app/deploy/update-native.sh v0.2.0     # a specific one, also how you go back
```

`update-native.sh` is a release file. The first time on a host that does not have it yet, fetch it from the latest release and run it (a private
repo needs a token that can read it, here the bot token):

```bash
sudo -n -u factory bash -c 'T=$(cat /srv/factory/secrets/github_token_bot); R=theninjadojo/software-factory
  ID=$(curl -fsSL -H "Authorization: Bearer $T" https://api.github.com/repos/$R/releases/latest | python3 -c "import json,sys; print([a[\"id\"] for a in json.load(sys.stdin)[\"assets\"] if a[\"name\"]==\"update-native.sh\"][0])")
  curl -fsSL -H "Authorization: Bearer $T" -H "Accept: application/octet-stream" https://api.github.com/repos/$R/releases/assets/$ID -o /tmp/update-native.sh
  bash /tmp/update-native.sh'
```

After that first update it is part of the install (`/srv/factory/app/deploy/update-native.sh`). It downloads the release's source tarball (a private repo needs a token: `SHIKUMI_TOKEN_FILE`, else `secrets/github_token_bot`, else
`github_token`, whichever can read the repo), pauses the factory (so no run can start during the update; the pause is lifted afterwards unless you had paused it) and refuses while an agent run is in flight, **checks the new code starts on the host first** (nothing
changes if it does not; the release workflow has already run the tests on that commit, and `SHIKUMI_RUN_TESTS=1` runs the whole suite on the host too), rebuilds the sandbox images, restarts the services and checks they stay up, and rolls back (previous code and
images) if they do not. For the UI banner on a private repo set `[updates] token_file` to a token that can read it.
`scripts/deploy.sh user@host` still deploys the working tree you have checked out (for development).

**From the UI (native installs).** Settings → **Updates** shows the running version and the latest release, with **Check now** (it reads the
release with the token the factory already has for the repository: `[updates] token_file`, else the `[github]` token), **Update to vX.Y.Z**
(starts `update-native.sh` as its own transient systemd unit, `shikumi-update-now`, so restarting the UI cannot kill it; its output is
shown on the page and kept in `state/update.log`) and the nightly auto-update: **Off**, **Patch releases** or **Every release**. Choosing
one installs `shikumi-update.service`/`.timer` for the factory user, writes `state/AUTO_UPDATE` (`patch` or `all`, read by `--auto`) and
enables the timer. The UI only offers this on a native install; Docker installs still use `scripts/update.sh`.

**Optional: update by itself, by hand.** Copy `deploy/systemd/shikumi-update.service` and `.timer` to `~factory/.config/systemd/user/` and run
`systemctl --user enable --now shikumi-update.timer`. Every night it runs `update-native.sh --auto`, which applies only a newer **patch**
release (same major.minor), or any newer release when `state/AUTO_UPDATE` says `all`, and quietly does nothing while an agent run is in flight. It tests
first and rolls back like a manual update. `journalctl --user -u shikumi-update.service` shows what it did. It is off unless you enable it.

## Not covered

- **Source checkouts** update with `git pull` and `docker compose --profile build build`. `scripts/update.sh` does both after asking (the pull uses `GITHUB_TOKEN` or `$FACTORY_HOME/secrets/github_token`, and falls back to git's own credentials without one); it then asks separately whether to restart the services (`docker compose up -d`), warning when an agent run is in flight, and the default is no.
- **Config or database changes** between versions are not migrated automatically: read the release notes before a major update.
