# Shikumi App

<!-- shikumi:summary -->

## Install

```sh
pip install shikumi-app        # or: uv tool install shikumi-app
```

## Use

```sh
shikumi-app hello Ada          # Hello, Ada.
shikumi-app hello Ada --shout  # HELLO, ADA.
shikumi-app --help
```

```python
from shikumi_app import Config, greet

greet("Ada")  # 'Hello, Ada.'
greet("Ada", config=Config(greeting="Hi"))  # 'Hi, Ada.'
```

Settings can also come from the environment: `SHIKUMI_APP_GREETING`, `SHIKUMI_APP_PUNCTUATION`.

## Develop

You need [uv](https://docs.astral.sh/uv/).

```sh
uv sync                                       # install
uv run shikumi-app hello Ada                  # run the CLI from the source
uv run pytest                                 # tests with coverage
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv build                                      # the wheel and sdist, in dist/
```

CI runs the tests on Python 3.12, 3.13 and 3.14. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Release

Releases are published to PyPI from GitHub releases: see [DEPLOY.md](DEPLOY.md).
