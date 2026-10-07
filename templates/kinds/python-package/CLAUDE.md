# Shikumi App

A Python package with a command-line tool, `shikumi-app`. Python 3.12 and newer, managed with uv, built with `uv_build`.

## How the code is organised

```
src/shikumi_app/
  __init__.py     the public API: exactly what __all__ lists (greet, Config, __version__)
  _core.py        the library's logic
  _config.py      Config, a frozen dataclass; Config.from_env() reads SHIKUMI_APP_* variables
  _cli.py         the Typer app (entry point shikumi-app = shikumi_app._cli:app)
  __main__.py     python -m shikumi_app
  py.typed        tells type checkers the package has types
tests/            pytest; test_public_api.py pins __all__
CHANGELOG.md      Keep a Changelog
```

## Conventions

- **Public versus private.** Everything a user may import is re-exported from `__init__.py` and listed in `__all__`. Every
  other module starts with an underscore and can change freely. Adding to `__all__` is a minor release; removing or
  changing something in it is a breaking change (a major release, or a minor one while the version is 0.x).
- **Versions** follow semantic versioning. The version lives in `pyproject.toml` only (`uv version --bump minor` changes it);
  `__version__` reads it from the installed package.
- **Changelog.** Every change a user would notice gets a line under `## [Unreleased]` in `CHANGELOG.md`, in the section
  that fits (Added, Changed, Deprecated, Removed, Fixed, Security).
- The CLI is thin: it parses arguments and calls the library. Logic and its tests belong in the library.
- Everything is typed; mypy runs in `--strict` mode. Public functions have docstrings; examples in them run as doctests
  (see `tests/test_core.py`).
- Tests must keep coverage at 90% or more (`fail_under` in `pyproject.toml`), on every supported Python version.
- Keep runtime dependencies few: each one is installed by every user.

## Commands

Installing needs the network; everything after that works offline.

```sh
uv sync                              # install from uv.lock (network)
uv run shikumi-app hello Ada         # run the CLI
uv run pytest                        # tests with coverage
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run ruff format .                 # fix formatting
uv build                             # wheel and sdist in dist/
uv run --python 3.12 pytest          # another Python version (downloads it the first time: network)
```

Adding a dependency: `uv add <package>` (or `uv add --dev <package>`), which updates `pyproject.toml` and `uv.lock`; this needs
the network. Commit both files.
