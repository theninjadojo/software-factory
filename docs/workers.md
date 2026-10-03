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

## Installing

The same way as the factory: a release one-liner, an environment-driven setup script, an update script with rollback.

1. **Factory host.** `FACTORY_WORKERS=1 FACTORY_WORKER_NAME=my-mac FACTORY_WORKER_CHECKS="org/app:web-test:any" ./scripts/setup.sh` (or, on an
   existing install, add `[workers]` and run `python3 -m factory.ctl workers add my-mac`). `setup.sh` writes `[workers]` and the checks, sets
   `COMPOSE_PROFILES=workers` in `.env` (so `docker compose up -d` and `scripts/update.sh` start and update the worker API like every other
   service), makes the token and prints it once. Native installs enable `deploy/systemd/factory-workers.service`; `scripts/deploy.sh` restarts it.
2. **Make the API reachable** from the worker: it listens on `127.0.0.1:8788`. Use `ssh -L 8788:127.0.0.1:8788 factory-host`, a VPN, or a TLS reverse
   proxy. Never expose it plainly: the token is the only credential.
3. **Worker machine.** `curl -fsSL https://github.com/theninjadojo/software-factory/releases/latest/download/install-worker.sh | bash`. This downloads
   `shikumi-worker.tar.gz` (`worker/` and `sandbox/android/`) into `./shikumi-worker` and runs `scripts/setup-worker.sh`:
   - asks for (or reads) `FACTORY_URL` and `WORKER_TOKEN`; the token goes to `secrets/token` with mode 0600;
   - writes `worker.toml` with the recipes you chose (`WORKER_RECIPES="web android"`); an existing `worker.toml` is never overwritten;
   - for `android`: pulls `ghcr.io/<owner>/shikumi-android:<version>` and tags it `factory-android:latest`, or builds it from `sandbox/android` if there is
     no prebuilt image (several GB). Skip with `SKIP_IMAGE=1`;
   - installs a launchd agent (macOS) or a systemd user service (Linux), filled in from `worker/service/`. Skip with `SKIP_SERVICE=1`;
   - runs `worker.py --check`: recipes exist, git is installed, and the orchestrator accepts the token (`POST /v1/ping`, which claims nothing).
4. **Add checks** on the factory (Settings → Workers, or `[[workers.checks]]`) that name a recipe the worker has. The Workers page lists the worker as online
   within seconds, and `python3 -m factory.ctl doctor` reports tokens, checks and when a worker last polled.
5. **Update** with `./scripts/update-worker.sh [vX.Y.Z]` on the worker: it refuses while a job is running (`FORCE=1` overrides), replaces `worker/` and
   `sandbox/`, pulls the matching Android image, restarts, runs `--check`, and rolls back if that fails. `worker.toml`, `secrets/` and `work/` are never touched.

The worker must be able to **clone** the repos it verifies, with its own credentials (a read-only token via a git credential helper, or an SSH key and
`WORKER_GIT_URL=git@github.com:{repo}.git`). Nothing the factory holds is sent to the worker except the validated patch, the base commit and a recipe name.

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
# required = false        # advisory: a failure is noted on the PR but never blocks
```
`fix_rounds = 1` (in `[workers]`) gives the agent that many extra attempts, with the log, when a required check fails.

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

**Phase 3: close the loop (done).**
- *Fix rounds* (`fix_rounds`, default 1, max 3): when a required check really **fails**, the agent runs again with the failing log in an
  untrusted `<worker_check>` wrapper (implementers only), like screen diffs and CI logs. It starts from a fresh clone, so it redoes the work
  knowing what failed. Only genuine test failures qualify: a missing worker, a timeout, an invalid result or a worker error is not something
  the agent can fix and fails the run directly. The loop is in `main.dispatch` and ends when a round passes or the rounds run out.
- *Advisory checks* (`required = false` on a `[[workers.checks]]` entry, or a fourth word `advisory` in the UI): a failure is noted on the
  PR and never blocks or triggers a fix round. A check that never runs is also only a note.
- *Settings -> Workers*: enabled, mode, fix rounds, the waits, and the checks list (`owner/name recipe platform [advisory]` per line).
  Enabling, switching to `warn`, and any change to the checks need the confirmation box. The listen address and token file stay in
  `config.toml` on purpose.

**Phase 4: recipes (web and Android done; iOS next).**
Recipes ship in `worker/recipes/` and are copied to the worker, outside any checkout, so the repo cannot alter them.

- `web-test.sh` (done): installs with the lockfile (`npm ci`, `pnpm install --frozen-lockfile` or `yarn install --frozen-lockfile`; a missing
  lockfile still runs, with a warning), runs `build` and `test` scripts when they exist (npm's placeholder `test` is ignored), then
  `playwright install chromium` and `playwright test` if a Playwright config exists. Options: `--dir <subfolder>`, `--no-playwright`.
  Exit 0 = every step that applied passed; exit 1 = a step failed (a *failure*, which gets a fix round); exit 2 = the recipe could not
  tell what to run (no `package.json`, nothing to run, bad option), which the factory sees as a worker problem and never retries.
  Tested with stub package managers (every branch) and with real npm against a cloned, patched repo.
- `android-test.sh` (done, stub-tested only): runs `./gradlew <task>` inside the `factory-android` container (`sandbox/android/`: a pinned JDK
  17 image plus the Android SDK; the command-line tools download is verified against a checksum you must supply, and the emulator and
  its system image are an opt-in build argument). The agent-written project runs only in that throwaway container: the worker's own uid,
  read-only root, no capabilities, no new privileges, memory/CPU/pid limits, and only two host mounts (the checkout, and a Gradle cache
  folder). It has a network, because Gradle must fetch dependencies. `--emulator` adds a single `--device /dev/kvm` (Linux only; Docker on a
  Mac has no KVM, so Macs run unit tests) and boots a headless AVD, runs the task, and returns a final screenshot even when the tests
  fail. Exit codes follow the web recipe: 1 = build or tests failed, 2 = the environment is wrong (no engine, image not built, no
  `gradlew`, no KVM, the emulator did not boot), which is never retried. **Not yet run against a real SDK, image or emulator**: the tests use
  stub `docker`, `gradlew`, `adb`, `emulator` and `avdmanager`.
- iOS (Xcode in a Tart VM, simulator tests, screenshots): next. It cannot be containerised: Xcode runs only on macOS.

The worker drops any returned PNG outside the orchestrator's size limits (16..1600 wide, up to 6000 high) rather than sending it:
one oversize full-page screenshot would otherwise make the whole result invalid and turn a passing run into an error.

**Phase 5 (only if needed):** parallel workers per platform with queue priorities; worker-side result caching by (repo, base_sha, patch hash).
