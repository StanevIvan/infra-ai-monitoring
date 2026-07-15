"""Tests for the sample collector.

psutil is mocked so tests are deterministic and never depend on the real
machine state. We assert on the *shape* of what's collected -- families,
kinds, units, labels -- rather than on live numbers.
"""

from unittest.mock import MagicMock, patch

from infra_monitor.collector import collect_samples
from infra_monitor.models import MetricKind


def _fake_psutil(mock):
    mock.cpu_times_percent.return_value = MagicMock(
        user=10.0, system=5.0, idle=80.0, iowait=5.0
    )
    mock.virtual_memory.return_value = MagicMock(
        percent=55.0, used=8_000_000_000, available=8_000_000_000
    )
    mock.swap_memory.return_value = MagicMock(percent=1.0, used=100_000_000)
    mock.disk_partitions.return_value = [MagicMock(mountpoint="/")]
    mock.disk_usage.return_value = MagicMock(percent=70.0)
    mock.disk_io_counters.return_value = MagicMock(read_bytes=1000, write_bytes=2000)
    mock.net_io_counters.return_value = {
        "eth0": MagicMock(
            bytes_sent=500, bytes_recv=600, packets_sent=5, packets_recv=6,
            errin=0, errout=0, dropin=0, dropout=0,
        )
    }
    return mock


def _by_name(samples):
    out = {}
    for s in samples:
        out.setdefault(s.name, []).append(s)
    return out


@patch("infra_monitor.collector.psutil")
def test_collects_all_expected_families(mock_psutil):
    _fake_psutil(mock_psutil)
    samples = collect_samples()
    names = {s.name for s in samples}
    assert {"cpu.percent", "cpu.user", "cpu.system", "cpu.idle", "cpu.iowait"} <= names
    assert {"mem.percent", "mem.used", "mem.available", "swap.percent"} <= names
    assert "disk.usage.percent" in names
    assert {"disk.read_bytes", "disk.write_bytes"} <= names
    assert {"net.bytes_sent", "net.bytes_recv"} <= names


@patch("infra_monitor.collector.psutil")
def test_cpu_percent_derived_from_idle(mock_psutil):
    _fake_psutil(mock_psutil)
    samples = _by_name(collect_samples())
    assert samples["cpu.percent"][0].value == 20.0  # 100 - idle(80)


@patch("infra_monitor.collector.psutil")
def test_counters_are_counters_gauges_are_gauges(mock_psutil):
    _fake_psutil(mock_psutil)
    by_name = _by_name(collect_samples())
    assert by_name["net.bytes_sent"][0].kind is MetricKind.COUNTER
    assert by_name["disk.read_bytes"][0].kind is MetricKind.COUNTER
    assert by_name["cpu.percent"][0].kind is MetricKind.GAUGE
    assert by_name["mem.used"][0].kind is MetricKind.GAUGE


@patch("infra_monitor.collector.psutil")
def test_network_samples_are_labeled_by_interface(mock_psutil):
    _fake_psutil(mock_psutil)
    by_name = _by_name(collect_samples())
    assert by_name["net.bytes_sent"][0].labels_map == {"interface": "eth0"}


@patch("infra_monitor.collector.psutil")
def test_disk_usage_labeled_by_mount(mock_psutil):
    _fake_psutil(mock_psutil)
    by_name = _by_name(collect_samples())
    assert by_name["disk.usage.percent"][0].labels_map == {"mount": "/"}


@patch("infra_monitor.collector.psutil")
def test_explicit_disk_paths_override_partition_enumeration(mock_psutil):
    _fake_psutil(mock_psutil)
    collect_samples(disk_paths=["/custom"])
    mock_psutil.disk_usage.assert_called_once_with("/custom")
    mock_psutil.disk_partitions.assert_not_called()


@patch("infra_monitor.collector.psutil")
def test_missing_iowait_is_tolerated(mock_psutil):
    _fake_psutil(mock_psutil)
    # Simulate Windows/macOS: cpu_times_percent has no iowait attribute.
    mock_psutil.cpu_times_percent.return_value = MagicMock(
        spec=["user", "system", "idle"], user=10.0, system=5.0, idle=85.0
    )
    names = {s.name for s in collect_samples()}
    assert "cpu.iowait" not in names
    assert "cpu.percent" in names


@patch("infra_monitor.collector.psutil")
def test_disk_io_none_is_tolerated(mock_psutil):
    _fake_psutil(mock_psutil)
    mock_psutil.disk_io_counters.return_value = None
    names = {s.name for s in collect_samples()}
    assert "disk.read_bytes" not in names