import doctest

import pytest

import shikumi_app._core
from shikumi_app import Config, greet


def test_greet() -> None:
    assert greet("Ada") == "Hello, Ada."


def test_greet_tidies_the_name() -> None:
    assert greet("  Ada   Lovelace ") == "Hello, Ada Lovelace."


def test_greet_shouts() -> None:
    assert greet("Ada", shout=True) == "HELLO, ADA."


def test_greet_uses_the_config() -> None:
    assert greet("Ada", config=Config(greeting="Hi", punctuation="!")) == "Hi, Ada!"


@pytest.mark.parametrize("name", ["", "   "])
def test_greet_refuses_an_empty_name(name: str) -> None:
    with pytest.raises(ValueError, match="empty"):
        greet(name)


def test_the_docstring_examples() -> None:
    assert doctest.testmod(shikumi_app._core).failed == 0
