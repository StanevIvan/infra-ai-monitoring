"""Alembic environment.

Two project-specific behaviours worth knowing about:

1. **The database URL never lives in alembic.ini.** That file is committed, so
   putting a real DSN there would leak credentials. The URL is read from the
   environment (``DATABASE_URL``), loaded from the same gitignored ``.env``
   the application uses, so there is exactly one secret store.

2. **The URL is rewritten for SQLAlchemy.** The app uses psycopg directly and
   stores a plain ``postgresql://`` DSN, but SQLAlchemy would resolve that to
   psycopg2, which this project does not install. ``sqlalchemy_url()``
   re-points it at psycopg 3. See infra_monitor.config for the detail.

There is no ORM, so ``target_metadata`` is None and ``--autogenerate`` is not
available: migrations are written by hand.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the application package importable when Alembic runs from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from infra_monitor.config import (  # noqa: E402
    ENV_DATABASE_URL,
    load_dotenv,
    redact_dsn,
    sqlalchemy_url,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No ORM models: hand-written migrations only.
target_metadata = None


def get_url() -> str:
    """Resolve the database URL from .env / the environment, for SQLAlchemy."""
    load_dotenv()
    dsn = os.environ.get(ENV_DATABASE_URL)
    if not dsn:
        raise RuntimeError(
            f"{ENV_DATABASE_URL} is not set. Add it to .env, e.g.\n"
            "  DATABASE_URL=postgresql://infra:password@localhost:5432/infra"
        )
    return sqlalchemy_url(dsn)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting (``alembic upgrade head --sql``)."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect and apply migrations."""
    url = get_url()
    # Log where we're pointed, with the password masked.
    print(f"alembic: connecting to {redact_dsn(url)}", file=sys.stderr)

    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url

    connectable = engine_from_config(
        section,
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
