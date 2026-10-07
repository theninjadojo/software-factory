from shikumi_app import Config


def test_defaults() -> None:
    assert Config.from_env({}) == Config()


def test_from_env() -> None:
    env = {"SHIKUMI_APP_GREETING": "Hi", "SHIKUMI_APP_PUNCTUATION": "!", "OTHER": "x"}
    assert Config.from_env(env) == Config(greeting="Hi", punctuation="!")
