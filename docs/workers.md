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
touch jobs it claimed. Bodies are capped (results 64 MB, everything else 64 KB).

| Call | Body | Reply |
|---|---|---|
| `POST /v1/claim` | `{"recipes": ["ios-test"], "platform": "macos", "version": 1, "unready": {"web-test": "Node.js 18 or newer is not installed"}, "stats": {"disk_free": 88000000000, "disk_total": 256000000000, "mem_avail": 11000000000, "load": 0.4, "cpus": 8}}` (`unready` and `stats` are optional) | `200` job, or `204` when nothing matches |
| `POST /v1/jobs/<id>/heartbeat` | `{}` or `{"progress": "<text>"}` | `200 {"cancel": false}`; `cancel: true` means stop and report nothing |
| `POST /v1/jobs/<id>/result` | `{"status": "passed"\|"failed"\|"error", "exit_code": 0, "log": "...", "artifacts": [{"name": "home-ios", "png_b64": "..."}]}` | `200` |

A job: `{"id", "repo", "base_sha", "patch", "recipe", "lease_seconds"}`. `patch` is the unified diff the orchestrator already
validated (protected paths, size, no symlinks). The worker clones the repo with *its own* read credentials, checks out
`base_sha`, applies the patch with `git apply`, and runs the recipe in that checkout.

Machine stats: each claim may carry `stats`, the free and total bytes of the disk the work dir is on, memory available and total, the load
average and the CPU count (anything the machine cannot read is left out; other keys and non-numbers are dropped). The factory keeps the
last report with the worker (a poll without one leaves it as it was) and shows it on the worker's own page, `/workers/machine?name=`,
and as a disk bar on its stop on the floor, rated by the same `[health]` thresholds as the server's disks.

Leases: a claimed job needs a heartbeat at least every `lease_seconds` (default 120). A job with no heartbeat is put back in the
queue (at most `max_attempts`, default 2) and then fails. A job nobody claims within `claim_wait_seconds` fails.

Progress: a heartbeat may carry `progress`, one short line (control characters removed, cut to 200 characters) shown on the Workers page
while the job runs and kept as its "Last step". It also extends the lease. The orchestrator ignores a changed line that arrives less than
5 seconds after the previous one, and an old worker that sends `{}` still works. A recipe can name its phase by printing a line that
starts with `##progress `; the worker sends the latest one as `recipe <name>: <text>`.

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
the real app, grouped by project. Nothing is gated: no patch, and a failing suite is not an error.

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
- **Failing tests open a ticket**: when a repo's newest run exits 1, the orchestrator opens one GitHub ticket on that repo, *Fix the failing
  Playwright tests in ...*, quoting the last 6000 characters of the log inside a fence marked untrusted. It has no label, so nothing starts
  until a person does. While that ticket is open, later failing runs open no other; once it is closed, the next failing run opens a new
  one. If GitHub refuses, it is tried again at the next poll.
- **Python suites**: a repo with no JavaScript Playwright config but a pytest suite in `tests/e2e/` (with a `requirements.txt`) runs as
  `pytest tests/e2e` in a throwaway virtualenv. A Python suite opts in by saving PNGs into the folder named by `E2E_SCREENS_DIR`, which the
  recipe sets (this repo's `tests/e2e/test_screens.py` does it for every page at three widths, at CSS pixels and at most 5900 px tall). Add
  `"--browser", "/usr/bin/chromium"` to the recipe's `command` to use a browser you already have instead of downloading one.
- **On the board**: when a run's result is accepted the screenshots are also stored as Screens-board images under the run's commit
  (`jobs.complete` -> `captures.import_run`). A name like `tickets-phone-1234567890` becomes the page `tickets` at the view `phone` (a
  trailing checksum is dropped; a trailing desktop, tablet, phone, mobile or browser name is the view, anything else a page of its own).
  The canvas and the review tool read them from there, so notes and tickets work as for any board screen.
- **Test results and the HTML report**: the worker gives the recipe an empty folder outside the checkout (`FACTORY_RESULTS_DIR`). The
  wrapper config adds Playwright's JSON and HTML reporters writing there (`pw-<folder>.json`, `report-<folder>/`), and a Python suite
  runs with `--junitxml` (xunit1, so each test names its file). When the suite ends, failing or not, the worker reads them into one entry
  per test: an id (`path › describe › title` for Playwright, the pytest node id `path::Class::name` without parameters for pytest, the
  path relative to the repository root), pass or fail (a test that failed in any browser or parameter fails; flaky passes; skipped tests
  are left out) and the failure message without colour codes. It sends that list (up to 5000 tests, messages up to 2000 characters) and
  each report's `index.html` (up to 6 reports of 10 MB; attachments in its `data/` folder, such as traces, are not sent). If the result
  would pass the API's 64 MB, the reports are dropped first, then the messages. The orchestrator checks the list again (ids relative,
  without `..` in the path, unique) and keeps it with the run. Each test whose id is a scenario's *Playwright test* in the Tests
  register gives that scenario a result from the run (once per run; retired scenarios are skipped). On the board, the run line links
  **results** (every test, failures first, with its message and scenario) and **report** (Playwright's own report). The report is the
  worker's HTML and runs its own scripts, so it is served in a sandbox with an opaque origin (`sandbox allow-scripts`, no
  `allow-same-origin`): it cannot read the factory's cookies or pages, loads nothing from the network and submits no form. A small fixed
  script gives it an in-memory `localStorage`, which an opaque origin lacks. A worker from before this release sends neither, and the
  run shows as before.
- **Which test took each screenshot**: the recipe writes `shots.tsv` to `FACTORY_RESULTS_DIR` (the file each PNG was copied from, a tab,
  its name in `screens-out/`), and the worker matches it with the screenshots Playwright's JSON report attaches to each test. A Python
  suite is given `E2E_SHOTS_FILE` (a file in the same folder) and appends one JSON object a line per PNG it keeps:
  `{"file": "<name in E2E_SCREENS_DIR>", "test": "<pytest node id>"}`. `request.node.nodeid` or `PYTEST_CURRENT_TEST` both work (the
  worker drops the parameters and a trailing ` (call)`, and makes the path relative to the repository, as for the JUnit ids); the last line
  for a file wins. A suite that writes nothing still runs; its screenshots are just not linked. The worker sends the test id with each
  screenshot only when it is one of the run's test results, and the orchestrator checks it again (the same rules as a test id, and it must
  be in the run's results): a bad one drops only the link, never the screenshot. The links are stored with the shot (a shot several tests
  took keeps them all; a run at the same commit replaces them) and go when the shot is pruned. A scenario's page then shows the
  screenshots its *Playwright test* took in the repository's newest run (up to 24), and the review page of a screenshot lists the
  scenarios of that repository whose *Playwright test* took it, with their latest result.
- **Limits**: up to 300 screenshots and 40 MB per run (larger than the 8 of an ordinary check; the API accepts a 64 MB result), each
  checked like any worker PNG (at most 1600 x 6000, 3 MB). The newest 3 finished runs of a repo are kept; older ones with their images,
  results and reports are deleted. A backup keeps the runs but blanks their results and reports, like their logs. A run is lower priority than a verification job: the queue serves checks first.
- **Security**: the suite is the repo's own code and runs on the worker like any recipe (see Security model). The wrapper config is
  generated by the recipe, outside the repo's control, but the project's test files and config are run as they are.

### Android screens

The same button works for an Android project, with the recipe `android-screens` (platform `linux`, or `any`) in the setup box instead of
`playwright-screens`. A worker installed with the `android` option has it already; on an older worker add it to `worker.toml`:
```toml
[recipes.android-screens]
command = ["/path/to/worker/recipes/android-test.sh", "--screens"]     # add "--dir", "android" for a subfolder
timeout_seconds = 3600
artifacts = ["screens-out/*.png"]
```
It runs in the same locked-down `factory-android` container as `android-test`. Without `--task`, it looks in the project's Gradle files and
version catalog for [Roborazzi](https://github.com/takahirom/roborazzi) (`recordRoborazziDebug`) or
[Paparazzi](https://github.com/cashapp/paparazzi) (`recordPaparazziDebug`). Both take screenshots in ordinary JVM tests under Robolectric or
layoutlib, so no emulator or KVM is needed and every screenshot test gives one PNG. A project with neither exits 2, saying so. Paparazzi's committed
images are deleted first, so only this run's show. `--task` names another Gradle task; the PNGs are still taken from
`build/outputs/roborazzi/`, `src/test/snapshots/images/` and `build/outputs/connected_android_test_additional_output/` in every module.
`--emulator` runs `connectedDebugAndroidTest` on the headless emulator (as `android-test --emulator`: `/dev/kvm` and an image built with
`WITH_EMULATOR=1`) and keeps what the tests saved as additional test output, or the emulator's last screen when they saved none. Names are the
module and the test (`app-com-example-hometest-home-<checksum>.png`). Exit codes as `android-test`: 0 passed, 1 tests failed (screenshots kept),
2 nothing to run or the environment is wrong. A screenshot wider than 1600 px (a tablet at full density) is dropped by the worker's size check.

## Tools the recipes need

A worker offers the factory only the recipes it can run. Each shipped recipe answers `--preflight` (exit 3 and one line per missing tool,
else exit 0), and the worker asks again every minute. A recipe that cannot run is left out of `recipes` in the claim, so the factory
never hands it a job; it is sent as `unready` instead, with the first reason, and the Workers page shows it under the worker's recipes
("playwright-screens not ready: Node.js 18 or newer is not installed"). `worker.py --check` prints the same as a warning (not a failure,
so an update is never rolled back over a missing tool). A recipe of your own with no `--preflight` is always offered.

- **web and screens** need Node.js 18 or newer with npm, and corepack or pnpm (Node 25 and newer no longer ship corepack; the recipes use
  `corepack pnpm` when there is a corepack and plain `pnpm` when not; yarn is used as it is, or through corepack when it is not installed).
- **android** needs Docker or Podman and the `factory-android` image. **ios** needs Tart and the `shikumi-ios` VM.

The worker can give itself a Node: `worker/install-node.sh` unpacks a pinned official Node 22 LTS into `tools/node` in the worker folder
(nothing outside it, no sudo), after checking its SHA-256 against the value in the script. The recipes put `tools/node/bin` first on
`PATH` when it exists. It runs by itself in three places, and each only when the machine has no usable Node of its own:
`setup-worker.sh` (when the recipes include `web` or `screens`; `SKIP_NODE=1` skips it), `update-worker.sh` after an update, and the
running worker the first time a JavaScript recipe is not ready (once per run; `install_tools = false` in `worker.toml` turns that off).
To move to a newer Node, change `VERSION` and the four checksums in `worker/install-node.sh`, then `./worker/install-node.sh --force`.

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

## Lockfiles for published packages

When a repo is built after the packages it installs were published (a second build, see `publish` and `depends_on` under
`[[projects]]` in the README), the agent can raise the version in `package.json` but cannot write the lockfile entry: that needs the
registry, and the sandbox has no network. A worker can do it:

```toml
[[workers.lockfiles]]
repo = "your-org/shop-web"
recipe = "lockfile-update"   # default
platform = "any"             # default
```

On the worker: `WORKER_RECIPES="lockfile" WORKER_NPM_TOKEN=... ./scripts/setup-worker.sh` (or add the recipe to an existing
worker.toml: `command = [".../worker/recipes/lockfile-update.sh", "--token-file", ".../secrets/npm_token"]`). The token is a
**read-only** packages token (GitHub Packages: a classic token with `read:packages` only). The recipe gives it only to the registry
host (`--registry`, default `npm.pkg.github.com`) in a temporary npmrc outside the checkout.

What happens: after the agent's patch passes the usual checks, the orchestrator sends the worker only its `package.json` and lockfile
changes, with the published package names. The worker checks out the default branch, applies them, and runs
`pnpm install --lockfile-only` then `pnpm update --lockfile-only <packages>` (npm: `--package-lock-only`) in each folder whose
lockfile covers a package.json using those packages. Nothing is installed and no package or repository script runs. It returns
the diff of `package.json` and lockfiles only. The orchestrator refuses a diff touching any other file, applies it with the same
patch checks as the agent's, and commits it with the agent's change. It is advisory: if no worker answers or the registry fails,
the PR is opened anyway and says the lockfile still needs refreshing. yarn is not handled.

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
- A lockfile job gets only manifest and lockfile changes on top of the default branch (never `.npmrc` or code), runs no scripts, and its
  diff may touch only those files. Its packages token reaches only the registry host the recipe was given.

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
