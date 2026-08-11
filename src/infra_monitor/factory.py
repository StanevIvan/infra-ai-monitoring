"""Repository construction — the one place that knows which backend to build.

This is the seam that makes the database swappable at *runtime* rather than
compile time. Callers ask for "a repository" and get whichever implementation
the configuration selected; nothing outside this module imports a concrete
repository class.

Kept separate from ``repository.py`` on purpose: the interface module must
not import implementations, or the dependency direction established in
Phase A (everything points inward, toward the abstraction) would reverse.
"""

from __future__ import annotations

from infra_monitor.config import POSTGRES, SQLITE, Settings
from infra_monitor.repository import MetricsRepository


def get_repository(settings: Settings) -> MetricsRepository:
    """Return a repository for the configured backend.

    Args:
        settings: resolved configuration (see ``Settings.from_env``).

    Returns:
        An object satisfying :class:`MetricsRepository`. It is *not* yet
        initialized — use it as a context manager, which creates the schema
        on entry and closes the connection on exit.

    Raises:
        NotImplementedError: the backend is recognized but not built yet.
        ValueError: the backend name is unknown.
    """
    if settings.db_backend == SQLITE:
        # Imported lazily so a future Postgres-only deployment need not care
        # about the SQLite module, and vice versa (see below).
        from infra_monitor.storage import SqliteRepository

        return SqliteRepository(settings.sqlite_path)

    if settings.db_backend == POSTGRES:
        # Imported inside the branch so SQLite users are never required to
        # install a PostgreSQL driver they don't use.
        from infra_monitor.postgres_storage import PostgresRepository

        # Settings validation guarantees database_url is present for this
        # backend, so no None can reach the constructor here.
        return PostgresRepository(settings.database_url or "")

    # Settings validation should catch this first; kept as a safety net.
    raise ValueError(f"Unsupported db_backend: {settings.db_backend!r}")
