"""Tests for the SQLite sample storage layer.

Each test gets its own in-memory database (":memory:") so tests are fast,
isolated, and never touch a real metrics.db file.
"""

from datetime import datetime, timedelta, timezone

import pytest

from infra_monitor.models import MetricKind, Sample
from infra_monitor.storage import SampleStorage


@pytest.fixture
def storage():
    with SampleStorage(":memory:") as store:
        yield store


def _sample(name="cpu.percent", value=10.0, *, kind=MetricKind.GAUGE,
            unit="percent", labels=None, when=None):
    return Sample.create(name, value, kind=kind, unit=unit, labels=labels,
                         timestamp=when or datetime.now(timezone.utc))


def test_save_and_get_recent_round_trip(storage):
    original = _sample("net.bytes_sent", 2048, kind=MetricKind.COUNTER,
                       unit="bytes", labels={"interface": "eth0"})
    storage.save(original)

    recent = storage.get_recent(10)
    assert len(recent) == 1
    got = recent[0]
    # Full fidelity: value, kind, unit, and labels all survive the round trip.
    assert got.name == "net.bytes_sent"
    assert got.value == 2048.0
    assert got.kind is MetricKind.COUNTER
    assert got.unit == "bytes"
    assert got.labels_map == {"interface": "eth0"}


def test_save_many_returns_count_and_persists_all(storage):
    samples = [_sample(f"m{i}", float(i)) for i in range(5)]
    n = storage.save_many(samples)
    assert n == 5
    assert len(storage.get_recent(100)) == 5


def test_get_recent_orders_newest_first(storage):
    base = datetime(2026, 7, 10, 9, 0, 0, tzinfo=timezone.utc)
    for i in range(3):
        storage.save(_sample("cpu.percent", float(i), when=base + timedelta(seconds=i)))
    recent = storage.get_recent(3)
    assert [s.value for s in recent] == [2.0, 1.0, 0.0]


def test_query_filters_by_name(storage):
    storage.save_many([
        _sample("cpu.percent", 1.0),
        _sample("mem.percent", 2.0),
        _sample("cpu.percent", 3.0),
    ])
    cpu = storage.query(name="cpu.percent")
    assert {s.value for s in cpu} == {1.0, 3.0}


def test_query_filters_by_time_window(storage):
    base = datetime(2026, 7, 10, 9, 0, 0, tzinfo=timezone.utc)
    for i in range(5):
        storage.save(_sample("cpu.percent", float(i), when=base + timedelta(minutes=i)))
    window = storage.query(since=base + timedelta(minutes=1),
                           until=base + timedelta(minutes=3))
    assert sorted(s.value for s in window) == [1.0, 2.0, 3.0]


def test_get_recent_on_empty_db_returns_empty_list(storage):
    assert storage.get_recent(10) == []