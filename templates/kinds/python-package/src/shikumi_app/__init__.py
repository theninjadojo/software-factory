"""Shikumi App.

The public API is what ``__all__`` lists. Modules whose names start with an underscore are private:
they can change in any release.
"""

from importlib.metadata import version

from shikumi_app._config import Config
from shikumi_app._core import greet

__all__ = ["Config", "__version__", "greet"]

__version__ = version("shikumi-app")
