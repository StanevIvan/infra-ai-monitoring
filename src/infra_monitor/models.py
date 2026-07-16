"""Data models for the Infrastructure AI Monitoring Platform."""


from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Optional, Union


class MetricKind(str, Enum):
    """Whether a metric is a point-in-time value or a monotonic total.

    GAUGE
        A value that can move up or down and is meaningful on its own
        (e.g. ``cpu.percent = 42.0``).
    COUNTER
        A value that only ever increases over the life of the host; the
        interesting quantity is its *rate of change*, not the raw number
        (e.g. ``net.bytes_sent``). Resets to 0 on reboot, so consumers must
        tolerate a counter going *backwards* between two samples.

    Subclassing ``str`` makes the enum trivially JSON/SQLite serializable:
    ``MetricKind.GAUGE == "gauge"`` and it stores as the plain string.
    """

    GAUGE = "gauge"
    COUNTER = "counter"


# Labels are stored internally as a canonical, sorted tuple of (key, value)
# pairs. That keeps a Sample immutable *and* hashable, and guarantees that two
# samples with the same logical labels compare equal regardless of input order.
Labels = tuple[tuple[str, str], ...]


def normalize_labels(labels: Union[Mapping[str, str], Labels, None]) -> Labels:
    """Return labels as a canonical, sorted tuple of ``(str, str)`` pairs."""
    if not labels:
        return ()
    items = labels.items() if isinstance(labels, Mapping) else labels
    return tuple(sorted((str(k), str(v)) for k, v in items))


@dataclass(frozen=True)
class Sample:
    """A single measurement of one metric at one point in time."""

    timestamp: datetime
    name: str
    value: float
    kind: MetricKind = MetricKind.GAUGE
    unit: str = ""
    labels: Labels = ()

    def __post_init__(self) -> None:

        # Fail fast at construction so no invalid Sample can exist downstream.
        if not self.name:
            raise ValueError("Sample.name must be a non-empty string")
        if not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
            raise ValueError(f"{self.name}: value must be a finite number, got {self.value!r}")
        if self.kind is MetricKind.COUNTER and self.value < 0:
            raise ValueError(f"{self.name}: counter values must be >= 0, got {self.value}")
        # The 0..100 invariant is no longer universal -- it only applies to
        # things actually measured as a percentage.
        if self.unit == "percent" and not (0.0 <= self.value <= 100.0):
            raise ValueError(f"{self.name}: percent must be 0..100, got {self.value}")

    @classmethod
    def create(
        cls,
        name: str,
        value: float,
        *,
        kind: MetricKind = MetricKind.GAUGE,
        unit: str = "",
        labels: Optional[Mapping[str, str]] = None,
        timestamp: Optional[datetime] = None,
    ) -> "Sample":
        """Convenience constructor: stamps UTC time and normalizes labels."""
        return cls(
            timestamp=timestamp or datetime.now(timezone.utc),
            name=name,
            value=float(value),
            kind=kind,
            unit=unit,
            labels=normalize_labels(labels),
        )

    @property
    def labels_map(self) -> dict[str, str]:
        """Labels as a plain dict (for display / serialization)."""
        return dict(self.labels)

    @property
    def series_key(self) -> str:
        """Stable identity of the time series this sample belongs to."""

        if not self.labels:
            return self.name
        label_str = ",".join(f"{k}={v}" for k, v in self.labels)
        return f"{self.name}{{{label_str}}}"