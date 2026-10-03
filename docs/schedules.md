# Scheduled jobs

A schedule fetches data on a timer, saves a snapshot, and opens a ticket on a repository. By default the ticket carries the
`factory:auto` label, so the normal pipeline runs: the analyst reads the data and proposes improvements, then the classifier
decides whether to continue to a build. It will not build when it is unsure or a question needs a person.

```toml
[[schedules]]
name = "umami-weekly"
repo = "your-org/shop-web"          # must be one of the configured repos
every = "7d"                        # or cron = "0 9 * * 1" (five fields, UTC); exactly one of the two
title = "Weekly analytics review ${date}"
instructions = "..."                # what to do with the data; ${name} and ${date} work here and in the title
labels = ["factory:analyze"]        # optional; default [auto].label. [] = just open the ticket
skip_if_open = true                 # default: no new ticket while the previous one is open
keep = 30                           # snapshots kept per schedule
max_data_chars = 30000              # of the data placed in the ticket

[schedules.source]
type = "umami"
base_url = "https://api.umami.is/v1"
website_id = "..."
token_file = "/srv/factory/secrets/umami_key"
days = 7
metrics = ["path", "referrer", "browser", "device", "country"]
```

## In the UI

Settings → **Schedules**: a list with each schedule's next and last run, a detail page with run history and the latest snapshot, and an
add/edit form with a **Test fetch** button (shows what the source returns now; saves nothing, opens no ticket). **Run now** queues a
request that the orchestrator runs at its next poll, ignoring the timer and dry-run. Saving writes the whole `schedules` list to the UI's
overrides file (a list there replaces the one in `config.toml`, so the first save copies hand-written schedules across). API keys are stored
in `<secrets dir>/schedule-<name>` (mode 0600) and never shown again.

## Sources

Fixed in `factory/schedules.py` (`SOURCES`); a schedule picks one and supplies settings.

- `umami`: stats for the period and the one before it, views per day and top-N lists. `auth = "api-key"` (Umami Cloud) or `"bearer"` (self-hosted).
- `http`: any HTTPS JSON endpoint: `url`, `method`, `headers`, `body`, and `token_file` with `header`/`prefix` for a credential.

To add one, write `fetch_x(src, now) -> dict`, register it in `SOURCES`, and list its required keys in `REQUIRED`.

## Behaviour

- Timing: a schedule never runs retroactively; its clock starts when the orchestrator first sees it. Failures retry every 30 minutes, 3 times, and alert.
- Nothing runs in dry-run mode. `python3 -m factory.ctl schedules` lists them; `schedules run <name>` runs one now.
- Snapshots are written to `<state dir>/schedules/<name>/`. Agents cannot read them; the ticket body carries the data.
- The data is untrusted (a referrer or page title can be set by anyone). It goes into the ticket only inside a fence labelled untrusted, capped in size,
  and the fetch happens on the host, outside the sandbox. The labels come only from the admin's config.
