from alembic import command

from shikumi_app.config import Settings
from tests.conftest import alembic_config


def test_migrations_match_the_models(settings: Settings) -> None:
    """Fails when a table changed in repositories/tables.py without a migration."""
    command.check(alembic_config(settings.database_url))
