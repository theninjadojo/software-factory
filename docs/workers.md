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
   existing install, add `[workers]` and run `python3 -m factory.ctl workers add my-mac`). `setup.sh` writes `[workers]` and the checks, makes the
   token and prints it once. The admin UI runs the worker API as its own child process while workers are enabled (it starts within seconds of turning
   them on in Settings, and stops when they are turned off), so there is no separate service to install or restart; updating the UI updates it.
   In Docker the `ui` container publishes it on `127.0.0.1:8788` (`FACTORY_WORKERS_BIND` / `FACTORY_WORKERS_PORT` in `.env`).
   Or from the UI: Settings → Workers → enable, then Workers → **Add a worker**. It creates the token (shown once) and prints the install command for the worker machine.
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

## Playwright screens (the Screens board's Build screens button)

A worker can also run a project's Playwright suite on request and send back what it screenshotted, so the **Screens** tab can show
the real app, grouped by project. Nothing is gated: no patch, no ticket, and a failing suite is not an error.

- **Everything is set up in the UI.** The **Set up Playwright runs** box at the bottom of the Screens tab (open until everything is ready)
  shows what is still to do, in order: turn workers on (Settings → Workers), add a worker with the recipe `screens` (Workers → *Add a
  worker* prints the install command for that machine), and tick the repositories whose suite the button may run. The tick boxes are
  saved to the overrides file like every other UI setting; `config.toml` is not touched. Per repo you can change the recipe name and the
  platform (defaults `playwright-screens` and `any`). The equivalent config, if you prefer a file:
  ```toml
  [[screens.captures]]
  repo = "your-org/shop-web"
  recipe = "playwright-screens"     # the default
  platform = "any"                  # the default
  ```
- **Worker machine**: the install command from the UI runs `scripts/setup-worker.sh` with `WORKER_RECIPES="screens"` (or `"web screens"`). To add the recipe to an existing `worker.toml` by hand:
  ```toml
  [recipes.playwright-screens]
  command = ["/path/to/worker/recipes/playwright-screens.sh"]      # add "--dir", "web" to run only one folder
  timeout_seconds = 3600
  artifacts = ["screens-out/*.png"]
  ```
- **What happens**: the button records a request; at its next poll the orchestrator queues one job per repo at the default branch's head
  (a repo that already has one waiting or running gets no second). The worker clones it, and the recipe finds every folder with a
  Playwright config, installs with the lockfile, installs Chromium, and runs the suite through a wrapper config *the recipe writes*
  that turns on a full-page screenshot at the end of every test, whatever the project's own config says. Every PNG is returned under a
  unique name from its test folder. Exit codes follow the web recipe: 0 passed, 1 install or tests failed (screenshots still kept),
  2 nothing to run.
- **Python suites**: a repo with no JavaScript Playwright config but a pytest suite in `tests/e2e/` (with a `requirements.txt`) runs as
  `pytest tests/e2e` in a throwaway virtualenv. A Python suite opts in by saving PNGs into the folder named by `E2E_SCREENS_DIR`, which the
  recipe sets (this repo's `tests/e2e/test_screens.py` does it for every page at three widths, at CSS pixels and at most 5900 px tall). Add
  `"--browser", "/usr/bin/chromium"` to the recipe's `command` to use a browser you already have instead of downloading one.
- **On the board**: when a run's result is accepted the screenshots are also stored as Screens-board images under the run's commit
  (`jobs.complete` -> `captures.import_run`). A name like `tickets-phone-1234567890` becomes the page `tickets` at the view `phone` (a
  trailing checksum is dropped; a trailing desktop, tablet, phone, mobile or browser name is the view, anything else a page of its own).
  The canvas and the review tool read them from there, so notes and tickets work as for any board screen.
- **Limits**: up to 300 screenshots and 40 MB per run (larger than the 8 of an ordinary check; the API accepts a 64 MB result), each
  checked like any worker PNG (at most 1600 x 6000, 3 MB). The newest 3 finished runs of a repo are kept; older ones and their images
  are deleted. A run is lower priority than a verification job: the queue serves checks first.
- **Security**: the suite is the repo's own code and runs on the worker like any recipe (see Security model). The wrapper config is
  generated by the recipe, outside the repo's control, but the project's test files and config are run as they are.

## Updating workers from the UI

Settings → Workers shows the release each worker runs. A worker that is behind the factory has an **Update to X.Y.Z** button, and there is a
switch to update them all by themselves. A worker always follows the release **the factory itself runs**, so the two stay compatible.

- The worker reports its release (its `VERSION` file) every time it polls. When it has nothing to do it asks the worker API, at most once a
  minute, whether to update (`POST /v1/update`); the answer is yes only if it is behind and a person pressed the button (or the switch is on).
- It fetches `VERSION`, `shikumi-worker.tar.gz`, `setup-worker.sh` and `update-worker.sh` of that release **through the worker API**
  (`POST /v1/release`), which reads them from GitHub with the factory's own token and caches them in `state/worker-release/`. The worker
  holds no GitHub credentials for the repository, and only those four files of the release the factory runs are ever served.
- It then runs its own `scripts/update-worker.sh` with the downloaded files (`SHIKUMI_ASSET_BASE`), which keeps `worker.toml`, `secrets/` and
  `work/`, checks the new code with `worker.py --check` and rolls back if that fails. The script's restart step is skipped; when it
  succeeds the worker exits and its service (systemd `Restart=always`, launchd `KeepAlive`) starts the new version. A worker not run by a
  service installs the files and keeps running the old code until you restart it.
- A failed update leaves the worker running as it was and is not retried for an hour. An update never starts while a job runs.
- A worker installed before this feature does not report a release and cannot ask: update it once by hand with `./scripts/update-worker.sh`.

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

**Phase 4: recipes (web, Android and iOS written; iOS and the Android emulator are stub-tested only).**
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
- `ios-test.sh` (done, **stub-tested only: never run against Tart or Xcode**): builds and tests inside a throwaway macOS VM, because Xcode runs only
  on macOS and cannot be containerised. [Tart](https://tart.run) runs macOS VMs on Apple Silicon. Per job the recipe clones a prepared golden VM
  (copy-on-write, fast), boots the clone headless, copies the checkout in (without `.git`) over `tart exec`, runs
  `xcodebuild test -scheme S -destination D CODE_SIGNING_ALLOWED=NO`, copies a final simulator screenshot out (even when tests failed), and
  deletes the clone, also when it is stopped by the worker (the worker now sends SIGTERM and waits 20 s before SIGKILL, so the cleanup runs;
  a leftover `shikumi-job-*` VM from a hard crash is deleted at the next job). The agent-written project never touches the Mac's files, keychain or
  signing identities and nothing survives the job. Simulator builds only: no identity is ever needed, so none should be in the image.
  Arguments (fixed in `worker.toml`, one recipe per project): `--scheme` (required), `--project` or `--workspace`, `--destination`
  (default `platform=iOS Simulator,name=iPhone 15`), `--dir`, `--image`, `--boot-timeout`. Everything that reaches the VM's command line is
  restricted to plain characters. Exit codes: 0 passed; 1 build or tests failed (`xcodebuild` 65 and other non-zero codes: gets a fix round);
  2 the environment is wrong (no `tart`, no golden image, the VM did not boot or its guest agent did not answer, `xcodebuild` usage errors 64/66/70-73),
  never retried. Limits: Apple Silicon only; Apple's licence allows two macOS VMs at once per Mac, and this uses one, so run **one worker per Mac**;
  there is no Intel-Mac or Linux path.

**iOS golden image** (once per Mac; about 30 GB). `brew install cirruslabs/cli/tart`, then
`tart clone ghcr.io/cirruslabs/macos-sonoma-xcode:latest shikumi-ios` (or pass `WORKER_IOS_PULL=1` to `setup-worker.sh`). Cirrus's images include Xcode,
simulators and the guest agent that `tart exec` needs. To add tools or simulators, `tart run shikumi-ios`, install them, and `tart stop shikumi-ios`.
Do not sign in to an Apple ID or add certificates: every job clones this image, so anything in it is visible to agent-written code. Check it by hand
before relying on it: `tart run --no-graphics shikumi-ios &`, then `tart exec shikumi-ios xcodebuild -version`.

The worker drops any returned PNG outside the orchestrator's size limits (16..1600 wide, up to 6000 high) rather than sending it:
one oversize full-page screenshot would otherwise make the whole result invalid and turn a passing run into an error.

**Phase 5 (only if needed):** parallel workers per platform with queue priorities; worker-side result caching by (repo, base_sha, patch hash).
