import os
from collections.abc import Mapping
from dataclasses import dataclass

ENV_PREFIX = "SHIKUMI_APP_"


@dataclass(frozen=True)
class Config:
    """Settings, with defaults. ``Config.from_env()`` reads them from SHIKUMI_APP_* environment variables."""

    greeting: str = "Hello"
    punctuation: str = "."

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "Config":
        defaults = cls()
        return cls(
            greeting=environ.get(f"{ENV_PREFIX}GREETING", defaults.greeting),
            punctuation=environ.get(f"{ENV_PREFIX}PUNCTUATION", defaults.punctuation),
        )
