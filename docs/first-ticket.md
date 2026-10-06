# Your first ticket

From a running factory to a reviewed pull request, in about fifteen minutes. The factory starts in **dry-run**: it logs what it
would do and writes nothing to GitHub until you turn dry-run off (step 5).

You need: `./scripts/setup.sh` finished, one repo the bot can write to, and a small low-risk issue (for example "Add a Contact link to the footer").

First, ask the factory to check itself:

```bash
docker compose run --rm -e FACTORY_CONFIG=/etc/factory/config.toml orchestrator python3 -m factory.ctl doctor
```

It checks the engine, the images, the credentials, the folders, the GitHub token, repo access and labels, and tells you what to fix.
(`ctl` is the factory's small operator command: `status`, `pause`, `resume`, `doctor`, `labels`, `screens baseline`.)

## 1. Check that it is running

```bash
docker compose ps
docker compose logs -f orchestrator
```

You should see three services up (proxy, orchestrator, ui) and a log line saying the factory is in dry-run. Open
http://127.0.0.1:8787 and sign in with the password you chose.

## 2. Ask for an analysis

Put the label `factory:analyze` on the issue. In dry-run the log shows the decision and nothing else happens. Live, an Analyst
comment with requirements and open questions appears on the issue within a minute or two.

## 3. Ask for a design

Add `factory:design`. If the ticket changes what people see, the designer writes the UX and, in a repo with Claude Design canvases
(`*.dc.html`), adds mockups on a draft PR.

- On the issue: a Designer comment with links to the mockup files.
- In the UI: the mockup images, inline on the run page.
- On GitHub: a draft PR holding the mockups and their preview images.
  (With `design_pr = false` under `[roles.designer]` there is no PR: the previews and the canvas downloads are on the ticket's Images page in the UI.)

Look at the mockups. They are what the builder is asked to match.

## 4. Build it

Add `factory:ready`. The builder is shown the mockups, edits the repo in a sandbox with no network, and the factory opens a PR. You
should see the PR linked on the issue, then the reviewer's comment (if the reviewer is enabled). On the run page, a Screens section
shows what was built.

**Held?** If the design stage ran but left no mockup, the build does not start and the issue says why. Add `factory:skip-mockup` to
build without one, then apply `factory:ready` again. The behaviour (block, warn or off) is under Settings, Design mockups, or
`[mockups]` in the config.

## 5. Go live

When the dry-run decisions look right, press **Go live** on the UI's home screen (tick the box first). It is one click back to dry run
from the same place. (The setting is also under Settings, General, or `dry_run` in `config/config.toml`.) Repeat steps 2 to 4 on the same issue. This is where the factory starts commenting and opening PRs.

## Optional: check screens on every build

The factory can render a page from the repo at desktop (1440x900) and phone (390x844) size and compare it with approved baseline
images. List the page in the config (trusted config, never the repo), create the baselines once, review them, and commit them:

```toml
[[screens.pages]]
repo = "your-org/site"
name = "home"
path = "index.html"
```

```bash
python3 -m factory.ctl screens baseline your-org/site ./site-checkout
# look at screens/baselines/*.png, then commit them
```

After that, a build that changes the page by more than 0.1 % of its pixels gets one fix round with the diff images, then fails. A
build that changes the baselines opens a draft PR with the `factory:screens-changed` label, so a person reviews the new images.

## If something looks wrong

Run `ctl doctor` first. Then:

| Symptom | Fix |
|---|---|
| Nothing happens after a label | Still in dry-run? Read the log. Does the person who added the label have write access to the repo? The factory ignores everyone else. |
| The label does not exist | `python3 -m factory.ctl labels`. The token needs Issues write. |
| No mockup images | The repo may have no Claude Design canvases to follow, or the render image is missing (`docker images`, look for `localhost/factory-render`). |
| The build was held | Re-run the design stage, or add `factory:skip-mockup`. |
| Rate limit | The ticket is requeued and the factory pauses by itself. `ctl status` shows the state. |
