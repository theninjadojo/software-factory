from shikumi_app._config import Config


def greet(name: str, *, shout: bool = False, config: Config | None = None) -> str:
    """A greeting for ``name``.

    >>> greet("Ada")
    'Hello, Ada.'
    """
    config = config or Config()
    name = " ".join(name.split())
    if not name:
        raise ValueError("name must not be empty")
    text = f"{config.greeting}, {name}{config.punctuation}"
    return text.upper() if shout else text
