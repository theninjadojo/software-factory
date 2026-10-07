from typing import Annotated

import typer

from shikumi_app import __version__
from shikumi_app._config import Config
from shikumi_app._core import greet

app = typer.Typer(help="Shikumi App.", no_args_is_help=True, add_completion=False)


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version and exit.")
    ] = False,
) -> None:
    """Shikumi App."""


@app.command()
def hello(
    name: Annotated[str, typer.Argument(help="Who to greet.")],
    shout: Annotated[bool, typer.Option("--shout", help="Greet in capitals.")] = False,
) -> None:
    """Greet someone."""
    try:
        typer.echo(greet(name, shout=shout, config=Config.from_env()))
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="NAME") from e
