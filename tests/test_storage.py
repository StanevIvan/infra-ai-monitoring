"""Tests for the SQLite storage layer.

Key idea: every test gets its own in-memory database (":memory:"), so tests
are fast, isolated from each other, and never touch a real metrics.db file.
"""

from datetime import datetime, timezone

import pytest

from infra_monitor.models import Metric
from infra_monitor.storage import MetricsStorage


@pytest.fixture
def storage():
    """Provide a fresh, initialized in-memory storage for each test."""
    with MetricsStorage(":memory:") as store:
        yield store
    # __exit__ on the context manager closes the connection automatically.


def make_metric(cpu=10.0, mem=20.0, disk=30.0) -> Metric:
    return Metric(
        timestamp=datetime.now(timezone.utc),
        cpu_percent=cpu,
        mem_percent=mem,
        disk_percent=disk,
    )


def test_save_and_get_recent_returns_saved_metric(storage):
    metric = make_metric(cpu=42.0)

    storage.save(metric)
    recent = storage.get_recent(limit=10)

    assert len(recent) == 1
    assert recent[0].cpu_percent == 42.0


def test_get_recent_respects_limit(storage):
    for i in range(5):
        storage.save(make_metric(cpu=float(i)))

    recent = storage.get_recent(limit=3)

    assert len(recent) == 3


def test_get_recent_orders_newest_first(storage):
    # Save three metrics with distinct, increasing cpu values.
    # Because inserts happen in order, the last one saved should come first.
    storage.save(make_metric(cpu=1.0))
    storage.save(make_metric(cpu=2.0))
    storage.save(make_metric(cpu=3.0))

    recent = storage.get_recent(limit=3)

    assert [m.cpu_percent for m in recent] == [3.0, 2.0, 1.0]


def test_get_recent_on_empty_db_returns_empty_list(storage):
    assert storage.get_recent(limit=10) == []


def test_metric_rejects_out_of_range_percent():
    with pytest.raises(ValueError):
        make_metric(cpu=150.0)
