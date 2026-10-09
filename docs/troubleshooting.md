# Troubleshooting

For a Docker install from a release (`./shikumi`) or from source. Run the commands below in the install folder. For the native
Podman install, see [operations.md](operations.md).

## Start here

```bash
# checks the engine, the GitHub token, every repo and the model credentials; changes nothing
docker compose run --rm -e FACTORY_CONFIG=/etc/factory/config.toml orchestrator python3 -m factory.ctl doctor

docker compose logs -f orchestrator     # what the factory decided, and why
docker compose logs ui                  # the web UI
docker compose ps                       # what is running
```

`ctl status` (the same command with `status` instead of `doctor`) shows the mode (dry-run or live), whether the factory is
paused, and its last decisions.

`./scripts/setup.sh` can be run again at any time. It keeps what it has already done (config, `.env`, secrets) and asks only for
what is missing. To redo a step, delete that file first: `config/config.toml`, `.env`, or a file under `/srv/factory/secrets/`.

## Installing

| What you see | What to do |
|---|---|
| `Install Docker (or Podman) first.` | Install Docker Engine and its Compose plugin from your distribution or [docs.docker.com](https://docs.docker.com/engine/install/). |
| `needs the docker compose plugin` | Install the Compose v2 plugin (`docker compose version` must work; the old `docker-compose` command is not enough). |
| `Docker is installed but not reachable` | Start Docker (`sudo systemctl enable --now docker`). If it runs, your user may not use it: `sudo usermod -aG docker $USER`, then log out and in again. |
| `setup.sh drives the Docker compose install` | You only have Podman. Use the native install in [operations.md](operations.md), or install Docker. |
| Asked for your `sudo` password at `Directories under /srv/factory` | Expected: setup creates `/srv/factory` (secrets, state, work) and gives it to uid 1000. Without sudo, put it in your home instead: `FACTORY_HOME=$HOME/shikumi-data ./scripts/setup.sh`. |
| Docker Desktop | Set `FACTORY_HOME` to a folder under your home directory, which Docker Desktop shares with its VM. `/srv` is not shared. |
| `shikumi already exists` | It is installed already. Update it with `shikumi/scripts/update.sh`, or move the folder away to start over. |
| Pulling `ghcr.io/theninjadojo/shikumi*` fails with `denied` or `unauthorized` | You are installing from a private fork. `docker login ghcr.io` with a token that can read packages, or build from source (README, option 2). |

## The GitHub token

The factory needs a fine-grained token for the account it acts as (a bot account is best). Repository access: only the repos it
works on. Permissions: **Contents**, **Issues** and **Pull requests** read and write, **Metadata** read, **Actions** read (for CI
feedback). It does not need Workflows or Administration. Create it at
[github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new).

| What you see | What to do |
|---|---|
| `GitHub rejected this token` | It is mistyped, expired or revoked. Make a new one. |
| `owner/repo: the token can only read it` | The token lacks write permissions. Give it Contents, Issues and Pull requests: read and write. |
| `owner/repo: not found` | Check the repo name, and that the token's *Repository access* includes it. For an organisation's repo, the organisation may need to approve fine-grained tokens first (organisation Settings → Personal access tokens). |
| Runs start failing weeks later, with GitHub errors | The token expired. Write the new one to `/srv/factory/secrets/github_token` (mode 600, owned by uid 1000), or replace it in the UI under Settings, then `docker compose restart orchestrator`. |
| `labels failed` during setup | The token needs Issues write. Then create the labels: `docker compose run --rm -e FACTORY_CONFIG=/etc/factory/config.toml orchestrator python3 -m factory.ctl labels`. |

## Model credentials

| What you see | What to do |
|---|---|
| `Anthropic answered 401` | The API key is wrong or revoked. Create one at [console.anthropic.com](https://console.anthropic.com/settings/keys). |
| `Anthropic answered 000` | This machine cannot reach `api.anthropic.com`. Check its network or proxy. |
| A subscription token is accepted, but runs fail | Setup cannot test a subscription token (`claude setup-token`). Make a new one and replace it in the UI under Settings. An API key is the supported route for unattended use. |

## The web UI

| What you see | What to do |
|---|---|
| `the UI did not answer yet` | Give it a minute, then read `docker compose logs ui`. |
| You cannot open it from another computer | By default it only listens on the machine itself. Use an SSH tunnel, `ssh -L 8787:127.0.0.1:8787 <host>`, then open http://127.0.0.1:8787. To open it on your network instead, set `FACTORY_UI_BIND=0.0.0.0` and `FACTORY_UI_ALLOWED_HOSTS=<the name or address you type>` in `.env`, then `docker compose up -d`. It is plain HTTP: only on a network you trust, never forwarded to the internet. |
| `421 Misdirected Request` | The address in the browser is not in `FACTORY_UI_ALLOWED_HOSTS`. Add it (comma separated), then `docker compose up -d`. |
| Forgot the password | `docker compose run --rm ui python3 -m factory.ui --config /etc/factory/config.toml --set-password` |
| Too many sign-in attempts | Failed sign-ins are rate-limited per address. Wait a few minutes. |

## After you label an issue

| What you see | What to do |
|---|---|
| Nothing happens | Still in dry-run? The log shows what it would have done; press **Go live** on the home screen. Was the label applied by someone with write access to the repo? The factory ignores labels from everyone else. Is the factory paused (`ctl status`)? |
| The label does not exist | Create the labels with `ctl labels` (see above). |
| `factory:needs-answers` | The agent asked questions. Answer them in the UI (Needs you), on Telegram or Slack; the ticket then continues. |
| `factory:failed` | The ticket has a comment saying why, and the run page in the UI has the log. Fix the cause and apply the label again. |
| `factory:waiting-release` | A packages PR has to be merged and published before the repos that use it are built. Merge it. |
| A build was held after the design stage | The designer left no mockup. Run the design stage again, or add `factory:skip-mockup`. |
| No mockup images | The repo has no Claude Design canvases to follow, or the render image is missing (`docker images`). |
| `rate limit` alerts | The model hit a plan or API limit. The ticket is requeued and the factory pauses itself for a while. |

The step-by-step first ticket, with what you should see at each step, is [first-ticket.md](first-ticket.md).

## Updating

| What you see | What to do |
|---|---|
| `An agent run is in flight` | `update.sh` refuses to restart over a running agent, because a restart would kill the run. Wait for it to finish (the UI shows what is running), then run it again. |
| The new version does not start | `update.sh` rolls back to the version you had. Read `docker compose logs`, and report it with the version numbers. |
| You want a specific version | `./scripts/update.sh v0.39.0`. `docker compose run --rm -e FACTORY_CONFIG=/etc/factory/config.toml orchestrator python3 -m factory.ctl version` shows what is running. |

## Still stuck?

Open an issue with the bug report template: the version, how you installed it, and the `ctl doctor` output. Remove tokens, chat
ids and host names first. Security problems go to a private report instead, as described in [SECURITY.md](../SECURITY.md).
