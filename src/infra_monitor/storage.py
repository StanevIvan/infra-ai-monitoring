"""SQLite storage layer for metrics.

Design choice: all raw SQL lives here and nowhere else in the codebase.
This keeps the rest of the app database-agnostic and makes it far easier
to swap SQLite for PostgreSQL in Month 2 (same interface, new implementation).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from infra_monitor.models import Metric

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    cpu_percent REAL NOT NULL,
    mem_percent REAL NOT NULL,
    disk_percent REAL NOT NULL
);
"""

_INSERT_SQL = """
INSERT INTO metrics (timestamp, cpu_percent, mem_percent, disk_percent)
VALUES (?, ?, ?, ?);
"""

_SELECT_RECENT_SQL = """
SELECT timestamp, cpu_percent, mem_percent, disk_percent
FROM metrics
ORDER BY timestamp DESC, id DESC
LIMIT ?;
"""


class MetricsStorage:
    """Thin wrapper around a SQLite database of metric snapshots.

    Usage:
        storage = MetricsStorage("metrics.db")   # or ":memory:" for tests
        storage.init_schema()
        storage.save(metric)
        recent = storage.get_recent(10)
        storage.close()

    Also works as a context manager:
        with MetricsStorage(":memory:") as storage:
            storage.save(metric)
    """

    def __init__(self, db_path: str | Path = "metrics.db") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)

    def init_schema(self) -> None:
        """Create the metrics table if it doesn't already exist."""
        with self._conn:
            self._conn.execute(_CREATE_TABLE_SQL)

    def save(self, metric: Metric) -> None:
        """Persist a single Metric snapshot."""
        with self._conn:
            self._conn.execute(
                _INSERT_SQL,
                (
                    metric.timestamp.isoformat(),
                    metric.cpu_percent,
                    metric.mem_percent,
                    metric.disk_percent,
                ),
            )

    def get_recent(self, limit: int = 10) -> list[Metric]:
        """Return the most recent `limit` metrics, newest first."""
        cursor = self._conn.execute(_SELECT_RECENT_SQL, (limit,))
        rows = cursor.fetchall()
        return [
            Metric(
                timestamp=datetime.fromisoformat(row[0]),
                cpu_percent=row[1],
                mem_percent=row[2],
                disk_percent=row[3],
            )
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MetricsStorage":
        self.init_schema()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
