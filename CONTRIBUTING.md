# Contributing

- Python 3.12+, standard library only. Please keep it that way: the orchestrator is a small, auditable program, and
  every dependency is attack surface for something that handles untrusted ticket text.
- `python3 -m unittest discover -s tests` must pass. UI changes also need the browser tests (Playwright, desktop and phone) in `tests/e2e`:
  `python3 -m venv .venv && .venv/bin/pip install -r tests/e2e/requirements.txt && .venv/bin/playwright install chromium && .venv/bin/pytest tests/e2e`
  (set `E2E_BROWSER=/usr/bin/chromium` to use a system Chromium). They run against a seeded database and a fake GitHub. Tests never start real containers or touch the network; fake the
  boundary (see `tests/test_runner.py` for a fake sandbox over local git remotes).
- A change to a trust boundary (sandbox flags, patch validation, which GitHub actions the orchestrator can take, what the
  agent sees) needs a test that fails without it. See SECURITY.md for what each boundary protects.
- Keep example configs and docs free of real org names, hosts, tokens and chat ids.

## Issues and pull requests

- Issues and pull requests from anyone are welcome. Please follow the [code of conduct](CODE_OF_CONDUCT.md).
- Factory labels on an issue only start work when a user with write access applies them; for everyone else they are ignored.
  Maintainers may let the factory's agents work on an issue. Its pull requests get the same review as yours.
- Open a pull request against `main`, and fill in the checklist in [docs/pull_request_template.md](docs/pull_request_template.md).
- Report security problems privately, as described in [SECURITY.md](SECURITY.md).
- The docs index is [docs/README.md](docs/README.md); `tests/test_docs.py` checks that links resolve.
