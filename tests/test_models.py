"""Tests for the Sample domain model."""

from datetime import datetime, timezone

import pytest

from infra_monitor.models import MetricKind, Sample, normalize_labels


def test_create_stamps_time_and_normalizes_labels():
    s = Sample.create("net.bytes_sent", 10, kind=MetricKind.COUNTER,
                      unit="bytes", labels={"interface": "eth0"})
    assert s.value == 10.0
    assert isinstance(s.value, float)
    assert s.kind is MetricKind.COUNTER
    assert s.labels == (("interface", "eth0"),)
    assert s.timestamp.tzinfo is not None  # timezone-aware


def test_normalize_labels_is_sorted_and_stringified():
    assert normalize_labels({"b": 2, "a": "x"}) == (("a", "x"), ("b", "2"))
    assert normalize_labels(None) == ()
    # Idempotent: normalizing an already-normalized tuple is a no-op.
    assert normalize_labels((("a", "x"),)) == (("a", "x"),)


def test_series_key_includes_labels():
    s = Sample.create("net.bytes_recv", 1, labels={"interface": "eth0"})
    assert s.series_key == "net.bytes_recv{interface=eth0}"
    assert Sample.create("cpu.percent", 1, unit="percent").series_key == "cpu.percent"


def test_equal_samples_are_hashable_and_equal_regardless_of_label_order():
    a = Sample.create("x", 1, labels={"a": "1", "b": "2"},
                      timestamp=datetime(2026, 7, 10, tzinfo=timezone.utc))
    b = Sample.create("x", 1, labels={"b": "2", "a": "1"},
                      timestamp=datetime(2026, 7, 10, tzinfo=timezone.utc))
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1  # usable in a set


def test_percent_out_of_range_rejected():
    with pytest.raises(ValueError):
        Sample.create("cpu.percent", 150.0, unit="percent")


def test_counter_negative_rejected():
    with pytest.raises(ValueError):
        Sample.create("net.bytes_sent", -1, kind=MetricKind.COUNTER, unit="bytes")


def test_empty_name_rejected():
    with pytest.raises(ValueError):
        Sample.create("", 1.0)


def test_non_percent_gauge_allows_large_values():
    # bytes are not bounded to 0..100
    s = Sample.create("mem.used", 8_000_000_000, unit="bytes")
    assert s.value == 8_000_000_000.0