"""The storage abstraction — the contract every backend implements.

This is Phase A of the PostgreSQL migration. Defining this Protocol makes the
Month-1 design promise explicit: instead of "all SQL lives behind one class,"
the rest of the app now depends on ``MetricsRepository`` — an interface —
rather than on any concrete database.

The SQLite implementation lives in ``storage.py`` (``SqliteRepository``). A
future ``PostgresRepository`` will implement this same shape, and nothing
outside the storage layer will need to change to switch between them.

Structural typing: implementations do **not** need to inherit from this
Protocol. They only need to match its shape. Marking it ``runtime_checkable``
lets tests assert conformance with ``isinstance(repo, MetricsRepository)``
(which checks that the methods exist).
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional, Protocol, runtime_checkable

from infra_monitor.models import Sample


@runtime_checkable
class MetricsRepository(Protocol):
    """Persistence contract for metric samples.

    Every backend (SQLite today, PostgreSQL next) provides these operations.
    Callers — the CLI now, a FastAPI service later — depend on this interface
    and never on a concrete database class.
    """

    @property
    def display_name(self) -> str:
        """Human-readable identity of the backing store, for reports/logs.

        e.g. a file path for SQLite, or ``host:port/dbname`` for PostgreSQL.
        Replaces reaching into a backend-specific private attribute.
        """
        ...

    def init_schema(self) -> None:
        """Ensure the schema exists (idempotent)."""
        ...

    def save(self, sample: Sample) -> None:
        """Persist a single sample."""
        ...

    def save_many(self, samples: Iterable[Sample]) -> int:
        """Persist many samples in one transaction; return the count."""
        ...

    def get_recent(self, limit: int = 50) -> list[Sample]:
        """Return the most recent ``limit`` samples, newest first."""
        ...

    def query(
        self,
        name: Optional[str] = None,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 1000,
    ) -> list[Sample]:
        """Return samples filtered by name and/or time window, newest first."""
        ...

    def close(self) -> None:
        """Release the underlying connection/resources."""
        ...

    def __enter__(self) -> "MetricsRepository": ...

    def __exit__(self, exc_type, exc_val, exc_tb) -> None: ...
