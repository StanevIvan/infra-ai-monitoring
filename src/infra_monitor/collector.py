"""Collects basic system metrics from the host machine using psutil.

Month 1 scope: CPU, memory, and disk usage only. Later months will add
per-process metrics, network I/O, and log-based signals.
"""

from __future__ import annotations

import psutil

from infra_monitor.models import Metric


def collect_metric(disk_path: str = "/") -> Metric:
    """Take one snapshot of current system resource usage.

    Args:
        disk_path: filesystem path to check disk usage for. Defaults to root.
                   On Windows use something like "C:\\\\".

    Returns:
        A Metric with the current CPU, memory, and disk usage percentages.
    """
    # interval=0.5 gives psutil a short window to measure CPU usage accurately.
    # Without an interval, the first call after process start can return 0.0.
    cpu_percent = psutil.cpu_percent(interval=0.5)
    mem_percent = psutil.virtual_memory().percent
    disk_percent = psutil.disk_usage(disk_path).percent

    return Metric.now(
        cpu_percent=cpu_percent,
        mem_percent=mem_percent,
        disk_percent=disk_percent,
    )
