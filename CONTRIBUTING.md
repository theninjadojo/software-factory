# Contributing

- Python 3.12+, standard library only. Please keep it that way: the orchestrator is a small, auditable program, and
  every dependency is attack surface for something that handles untrusted ticket text.
- `python3 -m unittest discover -s tests` must pass. Tests never start real containers or touch the network; fake the
  boundary (see `tests/test_runner.py` for a fake sandbox over local git remotes).
- A change to a trust boundary (sandbox flags, patch validation, which GitHub actions the orchestrator can take, what the
  agent sees) needs a test that fails without it. See SECURITY.md for what each boundary protects.
- Keep example configs and docs free of real org names, hosts, tokens and chat ids.
