"""Collects basic system metrics from the host machine using psutil.

Month 1 scope: CPU, memory, and disk usage only. Later months will add
per-process metrics, network I/O, and log-based signals.
"""

from __future__ import annotations

import psutil

from datetime import datetime, timezone
from typing import Optional, Sequence
from infra_monitor.models import MetricKind, Sample


def _clamp_percent(value: float) -> float:
    """Keep percenteges inside 0..100 despite psutil rounding jitt error."""
    return max(0.0, min(100.0, float(value)))


def collect_samples(
        disk_path: Optional[Sequence[str]] = None,
        timestamp: Optional[datetime] = None,
        cpu_interval: float = 0.5,
) -> list[Sample]:
    """ Take one snapshot of the host and return it as a list of Samples.
    
    Args:
        disk_paths: explicit filesystem paths to measure usage for. If None,
            every mounted partition is enumerated automatically.
        timestamp: shared timestamp for the whole cycle (defaults to now, UTC).
        cpu_interval: blocking window psutil uses to measure CPU accurately.
            Without a positive interval the first reading after start is 0.0.
    """

    ts = timestamp or datetime.now(timezone.utc)
    out: list[Sample] = []

    def gauge(name: str, value: float, unit: str = "", labels= None) -> None:
        out.append(
            Sample.create(
                name,
                value,
                kind=MetricKind.GAUGE,
                unit=unit,
                labels=labels,
                timestamp=ts,))
        
        _collect_cpu(gauge, cpu_interval)
        _collect_memory(gauge)
        _collect_disk_usage(gauge, disk_path)
        _collect_disk_io(counter)
        _collect_network(counter)

        return out
    

def _collect_cpu(gauge, interval: float) -> None:
    times = psutil.cpu_times_percent(interval=cpu_interval)
    gauge("cpu.user", _clamp_percent(times.user), unit="percent")
    gauge("cpu.system", _clamp_percent(times.system), unit="percent")
    gauge("cpu.idle", _clamp_percent(times.idle), unit="percent")

    iowait = getattr(times, "iowait", None)
    if iowait is not None:
        gauge("cpu.iowait", _clamp_percent(iowait), unit="percent")


def _collect_memory(gauge) -> None:
    vm = psutil.virtual_memory()
    gauge("mem.percent", _clamp_percent(vm.percent), unit="percent")
    gauge("mem.used", vm.used, unit="bytes")
    gauge("mem.available", vm.available, unit="bytes")

    swap = psutil.swap_memory()
    gauge("swap.percent", _clamp_percent(swap.percent), unit="percent")
    gauge("swap.used", swap.used, unit="bytes")


def _collect_disk_usage(gauge, disk_paths: Optional[Sequence[str]]) -> None:
    if disk_paths:
        mounts = list(disk_paths)
    else:
        mounts = [p.mountpoint for p in psutil.disk_partitions(all=False)]

    for mount in mounts:
        try:
            usage = psutil.disk_usage(mount)
        except (PermissionError, OSError):
            # e.g. an empty CD-ROM drive on windows, or a path that cannot be started.
            continue 
        
        gauge("disk.usage.percent", _clamp_percent(usage.percent), unit="percent", labels={"mount": mount})


def _collect_disk_io(counter) -> None:
    io = psutil.disk_io_counters(perdisk=False)
    if io is None:
        return 
    counter("disk.read_bytes", io.read_bytes, unit="bytes")
    counter("disk.write_bytes", io.write_bytes, unit="bytes")



def _collect_network(counter) -> None:
    per_nic = psutil.net_io_counters(pernic=True)

    for iface, io in per_nic.items():
        labels = {"interface": iface}
        counter("net.bytes_sent", io.bytes_sent, unit="bytes", labels=labels)
        counter("net.bytes_recv", io.bytes_recv, unit="bytes", labels=labels)
        counter("net.packets_sent", io.packets_sent, unit="packets", labels=labels)
        counter("net.packets_recv", io.packets_recv, unit="packets", labels=labels)
        counter("net.errin", io.errin, unit="count", labels=labels)
        counter("net.errout", io.errout, unit="count", labels=labels)
        counter("net.dropin", io.dropin, unit="count", labels=labels)
        counter("net.dropout", io.dropout, unit="count", labels=labels)
        
        