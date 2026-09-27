"""
env.py
------
Alembic migration environment, wired to this project's SQLAlchemy models
(app.db.models.Base.metadata, for --autogenerate) and configuration
(app.config.settings, for the actual database URL). The URL is read from
the application's own settings rather than duplicated in alembic.ini, so
there is exactly one place (the .env file / environment variables) that
defines which database both the running app and its migrations target.

Alembic runs synchronously, so this uses the sync psycopg driver
(settings.sync_database_url) even though the application itself runs an
async engine with asyncpg - see app/config/settings.py.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

import sys
import os

# Ensure the backend/ directory (the parent of both app/ and migrations/)
# is importable, so `from app...` works regardless of the working
# directory Alembic is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config.settings import get_settings  # noqa: E402
from app.db.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Used by `alembic revision --autogenerate` to diff the database's actual
# schema against what these models declare.
target_metadata = Base.metadata

settings = get_settings()
# Overrides whatever (if anything) is in alembic.ini's sqlalchemy.url with
# the application's own configured database URL, using the sync driver
# since Alembic's migration runner is synchronous.
config.set_main_option("sqlalchemy.url", settings.sync_database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode: emits SQL to stdout/a script
    without needing a live database connection (e.g. for generating a
    .sql file to hand to a DBA, or for environments without direct DB
    access from the migration runner).
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection - the normal path
    for `alembic upgrade head` / `alembic revision --autogenerate`.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
