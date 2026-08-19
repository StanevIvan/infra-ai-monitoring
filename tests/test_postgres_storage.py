"""Tests for the PostgreSQL repository.

Split deliberately in two:

* **Pure tests** exercise SQL construction and row mapping. They import the
  module (which tolerates a missing driver) and run anywhere.
* **Integration tests** need a real server and are skipped unless psycopg is
  installed *and* TEST_DATABASE_URL points at a migrated database. This is
  the point made in the migration plan: SQLite-passing SQL does not prove
  PostgreSQL-correct SQL, so the Postgres path needs a real Postgres.

Run the integration tests with::

    docker compose up -d
    alembic upgrade head
    TEST_DATABASE_URL=postgresql://infra:<pw>@localhost:5432/infra pytest
"""

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from infra_monitor.models import MetricKind, Sample
from infra_monitor.postgres_storage import (
    PostgresRepository,
    build_query,
    row_to_sample,
    to_params,
)
from infra_monitor.repository import MetricsRepository

T0 = datetime(2026, 7, 10, 9, 0, 0, tzinfo=timezone.utc)


def _sample(name="net.bytes_sent", value=2048.0, **kw):
    return Sample.create(
        name,
        value,
        kind=kw.get("kind", MetricKind.COUNTER),
        unit=kw.get("unit", "bytes"),
        labels=kw.get("labels", {"interface": "eth0"}),
        timestamp=kw.get("timestamp", T0),
    )


# --- pure: parameter binding ----------------------------------------------


def test_to_params_order_and_types():
    params = to_params(_sample(), json_adapter=dict)
    assert params[0] == T0  # datetime passed through, not a string
    assert params[1] == "net.bytes_sent"
    assert params[2] == 2048.0
    assert params[3] == "counter"  # enum value, not the enum
    assert params[4] == "bytes"
    assert params[5] == {"interface": "eth0"}  # dict for JSONB, not JSON text


def test_to_params_applies_the_json_adapter():
    marker = object()
    params = to_params(_sample(), json_adapter=lambda v: marker)
    assert params[5] is marker


def test_to_params_empty_labels():
    s = Sample.create("cpu.percent", 12.5, unit="percent", timestamp=T0)
    assert to_params(s, json_adapter=dict)[5] == {}


# --- pure: row mapping ----------------------------------------------------


def test_row_to_sample_round_trip():
    row = (T0, "net.bytes_sent", 2048.0, "counter", "bytes", {"interface": "eth0"})
    s = row_to_sample(row)
    assert s.timestamp == T0
    assert s.kind is MetricKind.COUNTER
    assert s.labels_map == {"interface": "eth0"}
    assert s.series_key == "net.bytes_sent{interface=eth0}"


def test_row_to_sample_handles_null_labels():
    row = (T0, "cpu.percent", 12.5, "gauge", "percent", None)
    assert row_to_sample(row).labels == ()


# --- pure: query building -------------------------------------------------


def test_build_query_no_filters():
    sql, params = build_query(limit=10)
    assert "WHERE" not in sql
    assert sql.rstrip().endswith("LIMIT %s;")
    assert params == [10]


def test_build_query_by_name():
    sql, params = build_query(name="cpu.percent", limit=5)
    assert "WHERE name = %s" in sql
    assert params == ["cpu.percent", 5]


def test_build_query_time_window():
    since, until = T0, T0 + timedelta(minutes=5)
    sql, params = build_query(since=since, until=until, limit=7)
    assert "timestamp >= %s" in sql and "timestamp <= %s" in sql
    assert params == [since, until, 7]


def test_build_query_uses_placeholders_never_interpolation():
    # Injection safety: the value must not appear in the SQL text.
    sql, params = build_query(name="'; DROP TABLE samples; --")
    assert "DROP TABLE" not in sql
    assert params[0] == "'; DROP TABLE samples; --"


def test_build_query_orders_newest_first():
    sql, _ = build_query()
    assert "ORDER BY timestamp DESC, id DESC" in sql


# --- integration: needs a real PostgreSQL ---------------------------------

TEST_DSN = os.environ.get("TEST_DATABASE_URL")


def _repo():
    pytest.importorskip("psycopg")
    if not TEST_DSN:
        pytest.skip("TEST_DATABASE_URL not set")
    return PostgresRepository(TEST_DSN)


def _unique(prefix: str) -> str:
    """A metric name unique to this test run.

    The integration database persists between runs, so rows accumulate.
    Assertions must therefore identify *this run's* data rather than assume
    an empty table -- and the tests must not delete anything, because
    TEST_DATABASE_URL may well point at a developer's working database.
    """
    return f"{prefix}.{uuid4().hex[:8]}"


def _live_sample(name: str, value: float = 1.0) -> Sample:
    """A sample stamped with the current time.

    Uses now() rather than the fixed T0 the pure tests share: get_recent()
    orders by timestamp, so a sample stamped in the past is not necessarily
    the newest row in a database that already holds data from earlier runs.
    """
    return Sample.create(
        name,
        value,
        kind=MetricKind.COUNTER,
        unit="bytes",
        labels={"interface": "eth0"},
        timestamp=datetime.now(timezone.utc),
    )


def test_postgres_repository_satisfies_the_interface():
    repo = _repo()
    try:
        assert isinstance(repo, MetricsRepository)
    finally:
        repo.close()


def test_save_and_get_recent_round_trip():
    marker = _unique("pg.test.roundtrip")
    with _repo() as repo:
        repo.save(_live_sample(marker, 4096.0))
        recent = repo.get_recent(1)
    # Stamped with now(), so this row is the newest in the table.
    assert recent[0].name == marker, (
        "get_recent(1) did not return the row just written; another writer, "
        "or a row with a later timestamp, is present"
    )
    assert recent[0].value == 4096.0
    assert recent[0].labels_map == {"interface": "eth0"}
    assert recent[0].kind is MetricKind.COUNTER


def test_save_many_and_query_by_name():
    name_a, name_b = _unique("pg.test.a"), _unique("pg.test.b")
    with _repo() as repo:
        repo.save_many(
            [
                _live_sample(name_a, 1.0),
                _live_sample(name_a, 2.0),
                _live_sample(name_b, 3.0),
            ]
        )
        rows = repo.query(name=name_a, limit=100)
    # Names are unique to this run, so the match is exact, not a superset.
    assert {r.value for r in rows} == {1.0, 2.0}
    assert all(r.name == name_a for r in rows)


def test_save_many_empty_is_a_noop():
    with _repo() as repo:
        assert repo.save_many([]) == 0


def test_connection_stays_open_after_a_write():
    """Regression: `with conn:` closes the connection in psycopg 3.

    Mirroring the sqlite3 transaction idiom here committed *and closed* the
    connection, so any read after a write raised "the connection is closed".
    Writes must use conn.transaction() instead.
    """
    with _repo() as repo:
        repo.save(_live_sample(_unique("pg.test.openconn"), 1.0))
        assert not repo._conn.closed, "write closed the connection"
        repo.save_many([_live_sample(_unique("pg.test.openconn"), 2.0)])
        assert not repo._conn.closed, "batch write closed the connection"
        # And a read still works afterwards.
        assert repo.get_recent(1)


def test_write_then_read_then_write_interleaved():
    """The collector loop writes and reads repeatedly on one connection."""
    marker = _unique("pg.test.interleaved")
    with _repo() as repo:
        for i in range(3):
            repo.save(_live_sample(marker, float(i)))
            rows = repo.query(name=marker, limit=100)
            assert len(rows) == i + 1


def test_failed_write_rolls_back_and_keeps_connection_usable():
    """A constraint violation must roll back without killing the session."""
    psycopg = pytest.importorskip("psycopg")
    with _repo() as repo:
        # value is NOT NULL, so this insert must be rejected by the database.
        # pytest.raises both narrows the exception type and *asserts* the
        # failure happens - the previous try/except/pass would have passed
        # silently if the constraint were ever dropped.
        with (
            pytest.raises(psycopg.Error),
            repo._conn.transaction(),
            repo._conn.cursor() as cur,
        ):
            cur.execute(
                "INSERT INTO samples (timestamp, name, value, kind, unit) "
                "VALUES (%s, %s, %s, %s, %s);",
                (T0, "pg.test.bad", None, "gauge", ""),
            )
        # The connection must still be usable after the rollback.
        assert not repo._conn.closed
        marker = _unique("pg.test.after_rollback")
        repo.save(_live_sample(marker, 5.0))
        assert repo.query(name=marker, limit=1)


def test_display_name_is_redacted():
    repo = _repo()
    try:
        assert "***" in repo.display_name or "@" not in repo.display_name
    finally:
        repo.close()
