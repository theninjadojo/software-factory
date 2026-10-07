import subprocess
import sys

import pytest
from typer.testing import CliRunner

from shikumi_app import __version__
from shikumi_app._cli import app

runner = CliRunner()


def test_hello() -> None:
    result = runner.invoke(app, ["hello", "Ada"])
    assert result.exit_code == 0
    assert result.output == "Hello, Ada.\n"


def test_hello_shout_and_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHIKUMI_APP_GREETING", "Hi")
    result = runner.invoke(app, ["hello", "Ada", "--shout"])
    assert result.output == "HI, ADA.\n"


def test_hello_refuses_an_empty_name() -> None:
    result = runner.invoke(app, ["hello", " "])
    assert result.exit_code == 2
    assert "empty" in result.output


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_runs_as_a_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "shikumi_app", "hello", "Ada"], capture_output=True, text=True, check=True
    )
    assert result.stdout == "Hello, Ada.\n"
