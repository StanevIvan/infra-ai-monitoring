"""Data models for the Infrastructure AI Monitoring Platform."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Metric:
    """A single snapshot of system metrics at a point in time.

    Kept intentionally small and flat for month 1: one row = one snapshot.
    We'll extend this (per-process metrics, tags, etc.) in later months.
    """

    timestamp: datetime
    cpu_percent: float
    mem_percent: float
    disk_percent: float

    def __post_init__(self) -> None:
        for name in ("cpu_percent", "mem_percent", "disk_percent"):
            value = getattr(self, name)
            if not (0.0 <= value <= 100.0):
                raise ValueError(f"{name} must be between 0 and 100, got {value}")

    @staticmethod
    def now(cpu_percent: float, mem_percent: float, disk_percent: float) -> "Metric":
        """Convenience constructor that stamps the current UTC time."""
        return Metric(
            timestamp=datetime.now(timezone.utc),
            cpu_percent=cpu_percent,
            mem_percent=mem_percent,
            disk_percent=disk_percent,
        )
