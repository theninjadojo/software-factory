<!-- TEMPORARY: a hand-off prompt for an agent on a Mac. DELETE THIS FILE before (or right after) merging PR #153. Not part of the product. -->

# Prompt for the agent on the Mac

Paste everything below the line into the agent. Fill in the two bracketed values first.

---

You are on an Apple Silicon Mac. Your job is to install a "verification worker" from the software-factory repo, connect it to a running factory, and test its iOS recipe against REAL hardware. That recipe was written on Linux and has never run against Tart or Xcode, so expect to find assumptions that are wrong. Your main deliverable is an honest report, plus fixes for what you find.

SETUP
- Repo: https://github.com/theninjadojo/software-factory (clone it). The iOS recipe is on branch `workers-ios-recipe` (PR #153); if that PR is merged, use main. Read docs/workers.md first (sections "Installing", "ios-test.sh", "iOS golden image") and worker/recipes/ios-test.sh.
- Factory URL: [FACTORY_URL, e.g. http://127.0.0.1:8788 via an SSH tunnel: ssh -L 8788:127.0.0.1:8788 <factory-host>]
- Worker token: [TOKEN from `python3 -m factory.ctl workers add my-mac` on the factory host]. Never print it in your report or commit it.
- Use this account for the work, not an admin one if you can avoid it. The worker runs agent-written code, so do not sign in to an Apple ID, and do not put certificates or credentials in any VM image.

RULES
- Ask me before anything that downloads more than 5 GB (the Xcode VM image is about 30 GB; check free disk first and tell me the number).
- Do not push to main and do not merge anything. If you fix something, do it on a new branch off `workers-ios-recipe` (or main), add or adjust tests in tests/test_ios_recipe.py where the stub encoded a wrong assumption, run `python3 -m pytest -q --ignore=tests/e2e`, and open a PR.
- Do not touch VMs or services you did not create.
- Do not commit this TEMP-mac-agent-prompt.md file into your own branch.

STEPS (report PASS / FAIL / FIXED for each, with the exact command and the relevant output)
1. Prerequisites: python3 >= 3.11, git, `brew install cirruslabs/cli/tart`. Confirm `tart --version`.
2. Golden image: `tart clone ghcr.io/cirruslabs/macos-sonoma-xcode:latest shikumi-ios` (after my OK). Then verify by hand: `tart run --no-graphics shikumi-ios &`, `tart ip --wait 120 shikumi-ios`, `tart exec shikumi-ios xcodebuild -version`, `tart stop shikumi-ios`.
3. Check these specific assumptions in the recipe against reality, and say which were wrong:
   a. `tart list` columns: the recipe takes the NAME as the 2nd column (`awk '{print $2}'`) and the header row is ignored. Show the real output.
   b. `tart exec <vm> true` works (guest agent), and `tart exec -i <vm> /bin/sh -c "tar -xf - -C /Users/admin/work"` accepts a tar stream on stdin.
   c. `tart ip --wait <seconds> <vm>` syntax, and that `tart run --no-graphics` stays in the foreground when backgrounded with `&`.
   d. The guest user and home really are `admin` and `/Users/admin` in this image.
   e. `xcodebuild test` exit codes: 65 for a failing test AND for a build error, and which codes appear for a wrong scheme or destination.
   f. `xcrun simctl io booted screenshot` still has a booted simulator after `xcodebuild test` finishes, so a screenshot is produced.
4. Make a tiny iOS project with a unit-test target (for example `brew install xcodegen` and a project.yml, or Xcode). Put it in a throwaway git repo.
5. Run the recipe BY HAND from that repo: `worker/recipes/ios-test.sh --scheme <S> --project <P>`. Test three cases: (i) passing tests exit 0, (ii) a deliberately failing test exits 1 and the log shows the failure, (iii) a wrong scheme exits 2. Confirm build/screens/final.png is a real PNG. Time each run, and note disk use and how long the clone and boot take.
6. Cleanup guarantees: after every run `tart list` must show no `shikumi-job-*` VM. Then run the recipe, kill it mid-xcodebuild with SIGTERM (`kill -TERM` its process group) and confirm the VM is deleted. Then SIGKILL one, confirm a VM is leaked, and confirm the NEXT run deletes it.
7. Install the worker the way a user would: `./scripts/setup-worker.sh` with FACTORY_URL, WORKER_TOKEN, WORKER_RECIPES="web ios", WORKER_IOS_SCHEME, WORKER_IOS_PROJECT. Confirm: worker.toml and secrets/token (mode 600) were written, the launchd agent com.shikumi.worker is installed and running (`launchctl print gui/$(id -u)/com.shikumi.worker`), the log is at worker.log, and `python3 worker/worker.py --config worker.toml --check` prints no FAIL. Then reboot-proof it: confirm the agent restarts after `launchctl kickstart -k`. Check the factory's Settings -> Workers page shows this worker online (ask me to confirm, or I will).
8. Web recipe on this Mac: run worker/recipes/web-test.sh against any small Node project and report.
9. If I give you a ticket/repo, trigger one real job end to end and report the factory's verdict. Otherwise stop after step 8.

REPORT FORMAT
A table of steps with PASS / FAIL / FIXED, then: (1) every assumption in the recipe that turned out wrong and what you changed, (2) anything surprising about timing, disk or reliability, (3) the PR link if you made one, (4) what you did NOT test. Do not claim something works unless you ran it.
