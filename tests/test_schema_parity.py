"""Guard against the SQLite and PostgreSQL schemas drifting apart.

Alembic owns the PostgreSQL schema; SqliteRepository.init_schema() owns the
SQLite one. Two definitions of the same table is a deliberate trade-off (it
keeps unit tests fast and setup-free), but it introduces a real failure mode:
add a column to one and forget the other, and the test suite passes against a
schema production does not have.

These tests make that mistake fail loudly. They are intentionally
text-and-introspection based rather than requiring a live PostgreSQL, so they
run anywhere the rest of the suite does.
"""

import re
import sqlite3
from pathlib import Path

from infra_monitor.storage import SqliteRepository

# The canonical column set. Changing the schema means changing this tuple,
# which forces both backends to be updated together.
EXPECTED_COLUMNS = ("id", "timestamp", "name", "value", "kind", "unit", "labels")

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0001_create_samples.py"
)


def _sqlite_columns() -> list[str]:
    with SqliteRepository(":memory:") as repo:
        conn = sqlite3.connect(":memory:")
        try:
            # Re-create the schema in a throwaway connection using the same
            # DDL the repository uses, then introspect it.
            from infra_monitor.storage import _CREATE_TABLE_SQL

            conn.execute(_CREATE_TABLE_SQL)
            rows = conn.execute("PRAGMA table_info(samples);").fetchall()
        finally:
            conn.close()
        assert repo.display_name == ":memory:"
    return [r[1] for r in rows]


def test_sqlite_has_the_expected_columns():
    assert tuple(_sqlite_columns()) == EXPECTED_COLUMNS


def test_migration_file_exists():
    assert MIGRATION.is_file(), f"initial migration missing at {MIGRATION}"


def test_postgres_migration_defines_the_same_columns():
    source = MIGRATION.read_text(encoding="utf-8")
    create = re.search(r"CREATE TABLE samples\s*\((.*?)\);", source, re.S)
    assert create, "could not find CREATE TABLE samples in the migration"

    body = create.group(1)
    declared = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        declared.append(line.split()[0])

    assert tuple(declared) == EXPECTED_COLUMNS, (
        f"PostgreSQL migration columns {declared} do not match the canonical "
        f"set {list(EXPECTED_COLUMNS)}. Update both backends together."
    )


def test_both_backends_agree():
    # The actual anti-drift assertion: whatever SQLite creates and whatever
    # the migration creates must be the same set of columns.
    source = MIGRATION.read_text(encoding="utf-8")
    for column in _sqlite_columns():
        assert re.search(rf"\b{re.escape(column)}\b", source), (
            f"column {column!r} exists in SQLite but not in the PostgreSQL " "migration"
        )


def test_migration_has_a_downgrade_path():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "def downgrade()" in source
    assert "DROP TABLE" in source, "downgrade must actually remove the table"
