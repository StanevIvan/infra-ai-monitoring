"""Tests for the metric collector.

Key idea: we mock psutil rather than reading real CPU/memory/disk numbers.
A test should never depend on what your laptop happens to be doing right
now (e.g. "cpu_percent < 50" would randomly fail if a video call is open).
Mocking makes the test deterministic and fast.
"""

from unittest.mock import MagicMock, patch

from infra_monitor.collector import collect_metric


@patch("infra_monitor.collector.psutil")
def test_collect_metric_reads_expected_fields(mock_psutil):
    mock_psutil.cpu_percent.return_value = 12.5
    mock_psutil.virtual_memory.return_value = MagicMock(percent=55.0)
    mock_psutil.disk_usage.return_value = MagicMock(percent=70.0)

    metric = collect_metric()

    assert metric.cpu_percent == 12.5
    assert metric.mem_percent == 55.0
    assert metric.disk_percent == 70.0


@patch("infra_monitor.collector.psutil")
def test_collect_metric_uses_given_disk_path(mock_psutil):
    mock_psutil.cpu_percent.return_value = 1.0
    mock_psutil.virtual_memory.return_value = MagicMock(percent=1.0)
    mock_psutil.disk_usage.return_value = MagicMock(percent=1.0)

    collect_metric(disk_path="/custom")

    mock_psutil.disk_usage.assert_called_once_with("/custom")
