# Verification workers

The agent sandbox has no network and runs on Linux, so agents cannot install dependencies, build, or run tests, and some
toolchains cannot run there at all. A **verification worker** is a machine you own (a Mac, a Linux box with a network and an
emulator, a CI-style host) that checks an agent's patch *before the factory pushes anything*, using capabilities the
orchestrator host does not have.

## Where it is useful

| Capability | Value | Why a worker |
|---|---|---|
| iOS build and test (Xcode, simulator, signing) | highest | Runs only on macOS. No Linux workaround exists. |
| Android build and emulator tests | high | Gradle needs the network for dependencies; hardware-accelerated emulators are awkward in containers. |
| Web: install, build, unit and e2e tests (Playwright) | high | The sandbox cannot `npm install`. This is the "agents cannot run tests" gap and it is not Mac-specific. |
| Safari/WebKit fidelity | medium | WebKit on macOS is the real engine. |
| Screenshot comparison | low | `factory/screens.py` already does it on Linux. A worker only helps when screens need a simulator or device. |

Not worth it: the read-only roles (analyst, designer, architect, reviewer, project manager). They stay in the sandbox.

## Shape of the design

- **Pull-based.** The worker polls the orchestrator's worker API over HTTPS. It needs no inbound port, so a laptop behind NAT works.
- **Recipes live on the worker.** The orchestrator names a recipe (`ios-test`); the worker's own config says what that runs.
  A job never carries a command. Which recipe applies to which repo is trusted factory config (`[[workers.checks]]`), never
  ticket text, the classifier or the agent.
- **A gate, not a side channel.** After a patch is validated and applied to the clean clone (and screens pass), the factory
  enqueues one job per configured check for that repo and waits. Nothing is pushed until every check passes. It fails closed
  (`mode = "block"`): no worker, a timeout or a bad result fails the run. `mode = "warn"` pushes anyway and says so.
- **The result is untrusted data.** Status is a fixed set, logs are capped and stripped of control characters, screenshots must
  pass `png_ok`, names must match `[a-z0-9-]`. The orchestrator re-derives the verdict; the worker's words never reach GitHub raw.
- **Code on the worker is agent-written code.** Unlike the sandbox, a worker has a network and a real toolchain. Treat it as
  hostile territory (see Security).

```
orchestrator (run_task)                 worker API (factory.workerapi)           worker (your Mac)
  patch validated + applied
  verify.gate() --enqueue--> verify_jobs (SQLite, WAL)
  waits, polls the table                 <---- POST /v1/claim -----------------  polls every few seconds
                                         ----- job {repo, base_sha, patch, recipe} -->
                                                                                  clone base_sha, git apply patch,
                                         <---- POST /v1/jobs/N/heartbeat -------  run the local recipe
                                         <---- POST /v1/jobs/N/result ----------  status, log, PNGs
  validated verdict  <-- table
  all pass -> push + PR; any fail -> RunResult("failed", log)
```

## Protocol (version 1)

All requests are JSON over HTTP(S) with `Authorization: Bearer <worker token>`. A token identifies one worker; it can only
touch jobs it claimed. Bodies are capped (results 16 MB, everything else 64 KB).

| Call | Body | Reply |
|---|---|---|
| `POST /v1/claim` | `{"recipes": ["ios-test"], "platform": "macos", "version": 1}` | `200` job, or `204` when nothing matches |
| `POST /v1/jobs/<id>/heartbeat` | `{}` | `200 {"cancel": false}`; `cancel: true` means stop and report nothing |
| `POST /v1/jobs/<id>/result` | `{"status": "passed"\|"failed"\|"error", "exit_code": 0, "log": "...", "artifacts": [{"name": "home-ios", "png_b64": "..."}]}` | `200` |

A job: `{"id", "repo", "base_sha", "patch", "recipe", "lease_seconds"}`. `patch` is the unified diff the orchestrator already
validated (protected paths, size, no symlinks). The worker clones the repo with *its own* read credentials, checks out
`base_sha`, applies the patch with `git apply`, and runs the recipe in that checkout.

Leases: a claimed job needs a heartbeat at least every `lease_seconds` (default 120). A job with no heartbeat is put back in the
queue (at most `max_attempts`, default 2) and then fails. A job nobody claims within `claim_wait_seconds` fails.

## Configuration

Orchestrator (`config.toml`):

```toml
[workers]
enabled = true
listen = "127.0.0.1:8788"                          # the worker API; put TLS or a VPN/SSH tunnel in front of it
tokens_file = "/srv/factory/secrets/worker_tokens" # one "name token" per line, mode 0600
mode = "block"                                     # block: a failing/missing check fails the run; warn: push anyway, say so
lease_seconds = 120
claim_wait_seconds = 900
max_wait_seconds = 3600

[[workers.checks]]
repo = "your-org/shop-mobile"
recipe = "ios-test"
platform = "macos"        # a worker only claims a job whose platform it declares
```

Worker (`worker.toml` on the Mac, see `worker/worker.example.toml`): the server URL, a token file, the git URL template, and
`[recipes.<name>]` with a fixed `command` (argv, no shell), `timeout_seconds`, `artifacts` globs (PNGs, relative to the
checkout) and `env_passthrough` (the only environment variables the recipe sees).

## Security model

Controls that exist:

- Recipe commands and the repo-to-recipe mapping are operator config on each side; no ticket, classifier or agent text selects either.
- Per-worker bearer tokens, constant-time comparison, a worker can only act on its own claimed jobs.
- The patch is validated before it is queued, and re-applied on the worker with plain `git apply` into a fresh directory, git hooks
  disabled, environment scrubbed down to `env_passthrough`, a timeout and a process-group kill.
- Results are validated as above; screenshots are stored in `run_images` and shown only by the UI behind its login.

What it cannot protect (read this): a recipe runs **agent-written code on a networked machine**. A malicious patch can use the
worker's network, read anything the worker's account can read, and abuse any signing identity in its keychain. So:

- Run the worker as a dedicated unprivileged account (or a throwaway VM: Tart on Apple Silicon, one clean VM per job, is the
  recommended recipe for iOS) that holds no credentials except a read-only token for the repos it verifies.
- Keep signing identities and App Store credentials off it. Use debug/simulator builds only.
- Merge protection still applies: the worker only decides whether a PR is opened; a person reviews and merges.

## Plan

**Phase 1: the mechanism (done).**
1. `docs/workers.md` (this file); README roadmap, architecture, SECURITY rows.
2. `factory/jobs.py`: `verify_jobs` and `workers` tables; enqueue, atomic claim, heartbeat, complete, lease expiry, cancel, strict result validation.
3. `factory/workerapi.py`: the HTTP API above (`python3 -m factory.workerapi --config ...`), token auth.
4. `factory/config.py`: `[workers]` and `[[workers.checks]]`, validated.
5. `factory/verify.py` + `runner.py`: the gate after screens, before push; verdict, logs and screenshots onto the run.
6. `worker/worker.py` + `worker/worker.example.toml`: the reference worker (standard library only).
7. Tests for every piece: `tests/test_jobs.py`, `tests/test_workerapi.py`, `tests/test_verify.py`, `tests/test_worker.py`, config tests.

**Phase 2: operating it (done).**
Settings → Workers page (configured checks, workers with online/offline state, recent jobs, per-job log and screenshots, all escaped and
behind the login), screenshots from workers on the run page (`run_images` kind `verify`), a `worker_offline` Telegram event (jobs waiting
and no worker polled for 2 minutes; once when it starts and once when it clears), `ctl workers add`, the systemd unit, the compose
`workers` profile, and `deploy.sh` support.

**Phase 3: close the loop.**
One fix round when a check fails (the log goes to the agent, like screen diffs and CI), a per-check `required`/`advisory` flag,
per-repo check lists editable in Settings.

**Phase 4: recipes.**
Documented, tested recipes: web (`npm ci && npm test`, Playwright), Android (Gradle + AVD), iOS in a Tart VM (build, simulator
tests, screenshots). Optionally route verification-heavy tickets to a worker earlier (before the review stage).

**Phase 5 (only if needed):** parallel workers per platform with queue priorities; worker-side result caching by (repo, base_sha, patch hash).
