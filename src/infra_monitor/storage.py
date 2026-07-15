"""SQLite storage layer for metrics.

Design choice: all raw SQL lives here and nowhere else in the codebase.
This keeps the rest of the app database-agnostic and makes it far easier
to swap SQLite for PostgreSQL in Month 2 (same interface, new implementation).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from infra_monitor.models import MetricKind, Sample, normalize_labels


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    kind TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    labels TEXT NOT NULL DEFAULT '{}',
);
"""
# Indexes matter now: get_recent orders by timestamp, and series queries
# filter by name. Without these, every read is a full scan + sort.
_CREATE_INDEXES_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_metrics_timestamp_ts ON samples (timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_metrics_name_ts ON samples (name, timestamp);",
)

_INSERT_SQL = """
INSERT INTO metrics (timestamp, name, value, kind, unit, labels)
VALUES (?, ?, ?, ?, ?, ?);
"""

_SELECT_RECENT_SQL = """
SELECT timestamp, name, value, kind, unit, labels
FROM metrics
ORDER BY timestamp DESC, id DESC
LIMIT ?;
"""

def _labels_to_json(labels) -> str:
    """Serialize labels to canonical JSON (sorted keys, compact separators).
 
    Canonical form means two identical label sets always produce the same
    text, so they compare equal in SQL and could be indexed/grouped later.
    """
    return json.dumps(dict(labels), sort_keys=True, separators=(",", ":"))


def _row_to_sample(row: tuple) -> Sample:
    """Rebuild a Sample (a domain object, not a raw tuple) from a DB row.
    
    Reads pass back through Sample construction, so validation applies on the way out too --
    defense in depth against a corrupted row.
    """
    timestamp, value, kind, unit, labels_json = row
    return Sample(
        timestamp=datetime.fromisoformat(timestamp),
        name=name,
        value=value,
        kind=MetricKind(kind),
        unit=unit,
        labels=normalize_labels(json.loads(labels_json)),
    )



class SampleStorage:
    """Thin wrapper around a SQLite database of metric samples.
 
    Usage::
 
        with SampleStorage("metrics.db") as storage:
            storage.save_many(collect_samples())
            recent = storage.get_recent(50)
 
    Works without the context manager too (call ``init_schema`` / ``close``
    yourself). Note ``with self._conn:`` blocks below manage *transactions*
    (commit on success, rollback on error), not the connection lifecycle.
    """

    def __init__(self, db_path: Union[str, Path] = "metrics.db") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)

    def init_schema(self) -> None:
        """Create the samples table and its indexes if they don't already exist."""

        with self._conn:
            self._conn.execute(_CREATE_TABLE_SQL)
            for stmt in _CREATE_INDEXES_SQL:
                self._conn.execute(stmt)
    
    def save(self, sample: Sample) -> None:
        """Persist a single Sample."""
        with self._conn:
            self._conn.execute(
                _INSERT_SQL,
                self.to_row(sample)
                )
            
    def save_many(self, samples: Iterable[Sample]) -> int:
        """Persist many Samples in one transaction. Returns the count.
 
        A single collection cycle now produces dozens of samples; batching
        them into one ``executemany`` is both faster and atomic (all land or
        none do).
        """

        rows = [self.to_row(s) for s in samples]
        with self._conn:
            self._conn.executemany(
                _INSERT_SQL,
                rows,
            )
        return len(rows)
    
    def get_recent(self, limit: int = 50) -> list[Sample]:
        """Return the most recent ``limit`` samples, newest first."""
        cursor = self._conn.execute(
            _SELECT_RECENT_SQL,
            (limit,),
        )
        return [_row_to_sample(row) for row in cursor.fetchall()]
    
    def query(
            self,
            name: optional[str] = None,
            *,
            since: Optional[datetime] = None,
            until: Optional[datetime] = None,
            limit: int = 1000,
            ) -> list[Sample]:
        """Flexible read: filter by metric name and/or time window.
 
        All filters are optional and combine with AND. Results are newest
        first. Time bounds are compared as ISO-8601 text, which sorts
        chronologically because timestamps are always stored in UTC.
        """

        clauses: list[str] = []
        params: list = []
        
        if name is not None:
            clauses.append("name = ?")
            params.append(name)

        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())

        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until.isoformat())
        
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT timestamp, name, value, kind, unit, labels from samples "
             f"{where} ORDER BY timestamp DESC, id DESC LIMIT ?"
        )
        params.append(limit)

        cursor = self._conn.execute(sql, params)
        return [_row_to_sample(row) for row in cursor.fetchall()]
    
    @staticmethod
    def to_row(sample: Sample) -> tuple:
        return (
            sample.timestamp.isoformat(),
            sample.name,
            sample.value,
            str(sample.kind.value),
            sample.unit,
            _labels_to_json(sample.labels),
        )
    
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SampleStorage":
        self.init_schema()
        return self
    
    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        self.close()