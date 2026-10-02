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

### The UI

`systemctl --user enable --now factory-ui` after setting the password (see [ui.md](ui.md)). It listens on loopback; use
`ssh -L 8787:127.0.0.1:8787 host`. It writes `config.overrides.toml` next to `config.toml`, so both files must be readable by
the orchestrator and the proxy, and the UI must be able to write the overrides, the state directory and the secrets directory.

## Day to day

- Status: `python3 -m factory.ctl status` (mode, paused?, last decisions). Pause/resume: `ctl pause|resume`, or `/pause`
  and `/resume` on Telegram. A pause stops new work; it does not interrupt a running task.
- Rate limits: when the agent hits a plan or API limit the ticket is requeued and the orchestrator pauses for
  `rate_limit_backoff_seconds`.
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
