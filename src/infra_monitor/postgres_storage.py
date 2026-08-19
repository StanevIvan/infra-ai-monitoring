"""PostgreSQL storage layer — the production implementation.

Mirrors ``SqliteRepository`` (storage.py) behind the same
:class:`MetricsRepository` contract, so switching backends is a
configuration change rather than a code change.

Three differences from the SQLite implementation are worth knowing:

**Placeholders.** psycopg uses ``%s``; sqlite3 uses ``?``. This is exactly the
dialect difference the repository boundary exists to contain — no caller ever
sees it.

**Native types.** SQLite stores timestamps as ISO text and labels as a JSON
string, so that layer serializes and re-parses by hand. PostgreSQL has real
``TIMESTAMPTZ`` and ``JSONB``, and psycopg adapts ``datetime`` and ``dict``
directly in both directions. No manual encoding.

**Schema ownership.** SQLite creates its own tables on entry. Here Alembic
owns the schema, so ``init_schema()`` deliberately does *not* create anything
— it verifies the migration has been applied and fails with an actionable
message if not.

psycopg is imported at module scope but tolerantly: the module stays
importable without the driver so that the SQL and the row-mapping helpers can
be unit-tested anywhere. Attempting to actually connect without psycopg raises
a clear installation error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from infra_monitor.config import redact_dsn
from infra_monitor.models import MetricKind, Sample, normalize_labels

try:  # pragma: no cover - exercised by the absence/presence of the driver
    import psycopg
    from psycopg.types.json import Jsonb

    PSYCOPG_AVAILABLE = True
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]
    Jsonb = None  # type: ignore[assignment]
    PSYCOPG_AVAILABLE = False


INSTALL_HINT = (
    "The PostgreSQL backend requires psycopg 3. Install it with:\n"
    '    pip install -e ".[postgres]"\n'
    'or:  pip install "psycopg[binary]"\n'
    "(the [binary] extra bundles libpq, so no system PostgreSQL is needed)."
)

_INSERT_SQL = """
INSERT INTO samples (timestamp, name, value, kind, unit, labels)
VALUES (%s, %s, %s, %s, %s, %s);
"""

_SELECT_RECENT_SQL = """
SELECT timestamp, name, value, kind, unit, labels
FROM samples
ORDER BY timestamp DESC, id DESC
LIMIT %s;
"""

_SELECT_COLUMNS = "SELECT timestamp, name, value, kind, unit, labels FROM samples"

# Used by init_schema() to check the migration has run.
_TABLE_EXISTS_SQL = """
SELECT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = current_schema() AND table_name = 'samples'
);
"""


def to_params(sample: Sample, json_adapter: Any = None) -> tuple:
    """Convert a Sample into positional query parameters.

    ``labels`` is wrapped for JSONB adaptation. The wrapper is injectable so
    this stays a pure function that can be tested without psycopg installed;
    in production it is ``psycopg.types.json.Jsonb``.
    """
    adapt = json_adapter if json_adapter is not None else (Jsonb or (lambda v: v))
    return (
        sample.timestamp,  # psycopg adapts datetime -> TIMESTAMPTZ
        sample.name,
        sample.value,
        str(sample.kind.value),
        sample.unit,
        adapt(dict(sample.labels)),  # dict -> JSONB
    )


def row_to_sample(row: tuple) -> Sample:
    """Rebuild a Sample from a database row.

    psycopg returns a timezone-aware ``datetime`` and a ``dict`` for JSONB, so
    unlike the SQLite layer there is nothing to parse. Reads still pass back
    through ``Sample`` construction, keeping validation on the way out.
    """
    timestamp, name, value, kind, unit, labels = row
    return Sample(
        timestamp=timestamp,
        name=name,
        value=value,
        kind=MetricKind(kind),
        unit=unit,
        labels=normalize_labels(labels or {}),
    )


class PostgresRepository:
    """PostgreSQL-backed implementation of ``MetricsRepository``.

    Usage::

        with PostgresRepository(dsn) as repo:
            repo.save_many(collect_samples())
            recent = repo.get_recent(50)

    .. warning::

       **Never write ``with self._conn:`` here.** It is tempting, because in
       ``sqlite3`` (and in psycopg2) that idiom means "transaction: commit on
       success, roll back on error" and leaves the connection open. In
       **psycopg 3 it commits and then CLOSES the connection**, so the next
       read on this repository fails with "the connection is closed".

       Use ``with self._conn.transaction():`` for atomic writes. The
       connection is opened with ``autocommit=True``, so reads run without
       leaving an idle-in-transaction session behind.
    """

    def __init__(self, dsn: str) -> None:
        if not PSYCOPG_AVAILABLE:
            raise RuntimeError(INSTALL_HINT)
        if not dsn:
            raise ValueError("A PostgreSQL DSN is required (set DATABASE_URL).")
        self._dsn = dsn
        # A single connection suits the single-process collector. The FastAPI
        # service in Month 2 will need psycopg_pool instead - a change confined
        # to this class, because callers only know the interface.
        #
        # autocommit=True so that *reads* do not silently open a transaction
        # and leave the session "idle in transaction" between cycles, which
        # holds resources and blocks VACUUM on a long-running collector.
        # Writes are made atomic explicitly via conn.transaction().
        self._conn = psycopg.connect(dsn, autocommit=True)

    @property
    def display_name(self) -> str:
        """Connection description with the password masked."""
        return redact_dsn(self._dsn)

    def init_schema(self) -> None:
        """Verify the Alembic migration has been applied.

        Deliberately does not create tables: Alembic owns this schema. Without
        this check the first insert would fail with PostgreSQL's terse
        ``relation "samples" does not exist``; this turns that into an
        instruction.
        """
        with self._conn.cursor() as cur:
            cur.execute(_TABLE_EXISTS_SQL)
            row = cur.fetchone()
        if not row or not row[0]:
            raise RuntimeError(
                "The 'samples' table does not exist in the target database. "
                "Alembic owns the PostgreSQL schema - create it with:\n"
                "    alembic upgrade head"
            )

    def save(self, sample: Sample) -> None:
        """Persist a single Sample, atomically."""
        # NOTE: `with self._conn.transaction():`, never `with self._conn:`.
        # See the class docstring - the latter closes the connection.
        with self._conn.transaction():
            with self._conn.cursor() as cur:
                cur.execute(_INSERT_SQL, to_params(sample))

    def save_many(self, samples: Iterable[Sample]) -> int:
        """Persist many Samples in one transaction; return the count."""
        rows = [to_params(s) for s in samples]
        if not rows:
            return 0
        with self._conn.transaction():
            with self._conn.cursor() as cur:
                cur.executemany(_INSERT_SQL, rows)
        return len(rows)

    def get_recent(self, limit: int = 50) -> list[Sample]:
        """Return the most recent ``limit`` samples, newest first."""
        with self._conn.cursor() as cur:
            cur.execute(_SELECT_RECENT_SQL, (limit,))
            return [row_to_sample(r) for r in cur.fetchall()]

    def query(
        self,
        name: Optional[str] = None,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 1000,
    ) -> list[Sample]:
        """Return samples filtered by name and/or time window, newest first."""
        sql, params = build_query(name=name, since=since, until=until, limit=limit)
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return [row_to_sample(r) for r in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PostgresRepository":
        self.init_schema()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def build_query(
    name: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 1000,
) -> tuple[str, list]:
    """Build the filtered SELECT and its parameters.

    Split out as a pure function so the generated SQL can be asserted on (and
    executed against a real server) without needing a live connection.
    Parameters are always bound, never interpolated.
    """
    clauses: list[str] = []
    params: list = []
    if name is not None:
        clauses.append("name = %s")
        params.append(name)
    if since is not None:
        clauses.append("timestamp >= %s")
        params.append(since)
    if until is not None:
        clauses.append("timestamp <= %s")
        params.append(until)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"{_SELECT_COLUMNS}{where} ORDER BY timestamp DESC, id DESC LIMIT %s;"
    params.append(limit)
    return sql, params
