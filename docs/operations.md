# Operations

## Native install (rootless Podman + systemd)

```
/srv/factory/
  app/        the code (this repo's factory/, tests/, sandbox/, config.example.toml)
  config.toml your configuration
  secrets/    0600 files: github_token, claude.env, telegram_token, openrouter_key
  state/      factory.db, lock, PAUSED / pause_until
  work/       per-task workspaces (cleared on startup)
  run/        proxy.sock
```

Create a dedicated user (`factory`) with no sudo, enable lingering (`loginctl enable-linger factory`) so its user
services start at boot, install rootless Podman, build the sandbox image as that user
(`podman build -t factory-agent -f sandbox/Dockerfile sandbox`), copy `deploy/systemd/*.service` to
`~factory/.config/systemd/user/`, then `systemctl --user enable --now factory-proxy factory`.

Run `systemctl --user ...` as that user with `XDG_RUNTIME_DIR=/run/user/<uid>` set when using `sudo -u factory`.

### Deploying

`scripts/deploy.sh user@host` tars the checkout, runs the tests on the host, and restarts the service. Add `--image` when
`sandbox/` changed. It **refuses to restart while an agent run is in flight**, because a restart kills the run.

### Health monitoring

`factory-health.timer` runs `python3 -m factory.health` every 5 minutes as its own process, so it still reports when the
orchestrator is hung or dead. Install and start it like the other units:
`systemctl --user enable --now factory-health.timer`.

It sends a Telegram alert (event `health`, shown even at `quiet` verbosity) when a check turns bad or worse, repeats it every
`repeat_hours` while the problem lasts, and says so once when it recovers. Checks:

| Check | Warns / critical when |
|---|---|
| `disk:<path>`, `inodes:<path>` | free space or inodes low on the state dir, work dir or container storage (the usual way a factory stops) |
| `memory` | `MemAvailable` low (sandboxes are OOM-killed) |
| `orchestrator` | no completed poll for `stale_poll_minutes` (hung, crashed or failing every poll; includes the last error) |
| `database` | the SQLite file cannot be read |
| `stuck-runs` | a run is still "running" after twice `timeout_seconds` |
| `run-failures` | the last `failed_runs_warn` runs all failed (expired Claude token, API outage, broken image) |
| `engine`, `proxy` | `podman info` fails, or the egress proxy socket refuses connections |
| `secrets` | the GitHub token file or `claude.env` is missing |
| `github` | the token is rejected (401) or few API calls are left this hour |

`python3 -m factory.ctl health` (or `python3 -m factory.health --print`) shows every check and exits 1 if one is critical; it sends
nothing. The latest problems are also stored in the database's `status` table (`health`, `health_checked`).

A watchdog on the same machine cannot report that the machine is down. Set `[health] heartbeat_url` to a dead-man's-switch
service (healthchecks.io or similar): it is pinged on every run where nothing is critical, and that service alerts you when the pings stop.
It cannot see an expired Claude subscription token before a run fails; the `run-failures` check catches that after the fact.

### The UI

`systemctl --user enable --now factory-ui` after setting the password (see [ui.md](ui.md)). It listens on loopback; use
`ssh -L 8787:127.0.0.1:8787 host`. It writes `config.overrides.toml` next to `config.toml`, so both files must be readable by
the orchestrator and the proxy, and the UI must be able to write the overrides, the state directory and the secrets directory.

### Verification workers

Optional (see [workers.md](workers.md)). Set `[workers] enabled = true` and add `[[workers.checks]]`, create a token per worker
(`python3 -m factory.ctl workers add my-mac`, or **Workers → Add a worker** in the UI). The UI starts the worker API itself, as its own child
process, within seconds of workers being turned on, and stops it when they are turned off: there is no separate service. It listens on loopback (`127.0.0.1:8788`); give the Mac an SSH tunnel
(`ssh -L 8788:127.0.0.1:8788 host`), a VPN or a TLS proxy, and run `worker/worker.py` there. The **Settings → Workers** page shows who is
online, the queue, and each job's log and screenshots; Telegram sends `worker_offline` when jobs wait and no worker has polled for two
minutes. `scripts/deploy.sh` copies `worker/`; restarting the UI restarts the worker API (and it disables the `factory-workers.service` that older
installs used). For the Docker install, `FACTORY_WORKERS=1 ./scripts/setup.sh` does all of this; the `ui` container publishes 8788 on loopback. The worker machine installs with
`install-worker.sh` and updates with `update-worker.sh` (see [workers.md](workers.md#installing)); `python3 -m factory.ctl doctor` checks the factory side.

## Day to day

- Status: `python3 -m factory.ctl status` (mode, paused?, last decisions). Pause/resume: `ctl pause|resume`, or `/pause`
  and `/resume` on Telegram. A pause stops new work; it does not interrupt a running task.
- Fallback models: a route, role or `[review]` may list `fallback_models` (up to 3, same harness and effort). When the model hits a
  limit or a 5xx/overloaded API error, the next model is tried in a fresh run and a `fallback` alert is sent. Only the admin config
  sets the list; ticket text and the classifier cannot.
- Rate limits: when the agent hits a plan or API limit the ticket is requeued and the orchestrator pauses for
  `rate_limit_backoff_seconds`, once every fallback model is also limited. A chain that ends on a 5xx fails as before.
- Restarts: on startup the orchestrator clears leftover work directories and sandbox containers and requeues any ticket
  stuck in a `factory:working*` state.
- Settings changed in the UI apply when the orchestrator is next idle (it re-executes itself). `ls state/RESTART` shows one is pending.
- Agent prompts (`[prompts]`, or Settings → Agent prompts): your own standing instructions per agent, plus `all` for
  every agent. They go at the top of the prompt file and the built-in rules still win if they conflict. They are sent on
  every run of that agent, so keep them short: they cost tokens each time. Changing one needs the confirmation box ticked.
- Adding a repo: add it to `[github] repos` (standalone) or to a project in `[[projects]]`, create the factory labels in
  it, and make sure the bot account has write access and the token covers it. Restart.
- Rotating a secret: replace the file, restart the orchestrator.
- Tokens expire: a GitHub token that lapses shows up as failed runs and Telegram alerts.

## Docker notes

- The orchestrator starts sandboxes through the engine socket, and the engine resolves bind-mount paths **on the host**.
  The compose file therefore mounts `FACTORY_HOME/{state,work,run,secrets}` at the same absolute path inside the container.
  If you change `FACTORY_HOME`, change the paths in `config.toml` to match.
- Docker Desktop (macOS/Windows): put `FACTORY_HOME` under a directory shared with the VM (for example under your home
  directory), not `/srv`.
- Rootless Docker: set `DOCKER_SOCK=/run/user/$UID/docker.sock` in `.env` and `DOCKER_GID` to that socket's group.
- The container name filter used for recovery is anchored (`^factory-`) so it can never match the orchestrator or proxy
  containers themselves.

## Pitfalls we hit (so you don't)

- **Do not add sandboxing options to the user service** (`PrivateTmp`, `ProtectSystem`, `NoNewPrivileges`). In a systemd
  *user* manager they put the service in its own user namespace, and rootless Podman then fails with `cannot clone`.
- **Never restart while a run is in flight**: it kills the run. Check `podman ps --filter 'name=^factory-'` first (the
  deploy script does).
- **Do not run a second proxy**: it replaces the socket file under the first.
- **Don't `pkill -f` a pattern that appears in your own command line.**
- **A rebuilt image** is only needed when `sandbox/` changes; code changes need only a restart.
- **Unit tests are safe to run anywhere**: they never start containers or use the network.

## Verifying a sandbox by hand

The lock-down can be checked with the real command builder: construct `sandbox_cmd(...)`, replace the entrypoint with
`sh -c '...'`, and confirm direct network access fails, the proxy denies unlisted hosts, the root filesystem is read-only,
and `CapEff` is zero. Both engines were verified this way.

## Claude plan usage

With a subscription token in `claude.env`, the orchestrator asks `api.anthropic.com/api/oauth/usage` (at most every 5 minutes)
for the 5-hour session and 7-day week utilization. The Factory page shows both percentages, Telegram's `/usage` shows them with
reset times, and a `rate_limit` alert goes out once when either window reaches 90%. This endpoint is not a documented API and a
`setup-token` may lack the scope for it: the meter then shows nothing (or the last numbers, marked `?`). An API key has no plan
limits, so nothing is shown. Per-run token counts are on each run's page.
