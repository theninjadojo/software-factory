import shikumi_app


def test_the_public_api() -> None:
    """Changing this list is a change to the public API: see CHANGELOG.md and semantic versioning."""
    assert sorted(shikumi_app.__all__) == ["Config", "__version__", "greet"]
    for name in shikumi_app.__all__:
        assert hasattr(shikumi_app, name)
