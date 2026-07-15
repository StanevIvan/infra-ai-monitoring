"""Command-line entrypoint for the Infrastructure AI Monitoring Platform.

Collects a snapshot of system metrics, persists it, and prints a small
monitoring report of the most recent snapshots.

Run it in any of these ways:

    # as a module (recommended, from the project root)
    python -m infra_monitor.cli

    # directly (e.g. the "Run" button in VS Code)
    python src/infra_monitor/cli.py

    # if installed with `pip install -e .`
    infra-monitor

By default it monitors continuously, printing a fresh report every
--interval seconds until you stop it with Ctrl+C. Use --once for a single
snapshot.

Common options:

    python -m infra_monitor.cli                      # monitor until Ctrl+C
    python -m infra_monitor.cli --interval 5         # report every 5 seconds
    python -m infra_monitor.cli --once              # one snapshot, then exit
    python -m infra_monitor.cli --limit 10          # show last 10 snapshots
    python -m infra_monitor.cli --no-save           # don't write to the db
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

# --- Make direct execution work (python src/infra_monitor/cli.py) ----------
# When run as a plain script, the "infra_monitor" package isn't on sys.path,
# so the imports below would fail. Add the "src" directory (this file's
# grandparent) to sys.path so the package resolves either way.
try:
    from infra_monitor.collector import collect_samples
    from infra_monitor.models import MetricKind, Sample
    from infra_monitor.storage import SampleStorage
except ModuleNotFoundError:  # pragma: no cover - exercised only on direct run
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from infra_monitor.collector import collect_samples
    from infra_monitor.models import MetricKind
    from infra_monitor.storage import SampleStorage



def human_bytes(n: float) -> str:
    """Render a byte count as a human-readable string (B, KB, MB, ...)"""

    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024.0 or unit == "PB":
            return f"{value:,.0f} B" if unit == "B" else f"{value:,.1f} {unit}"

        value /= 1024.0
    return f"{value:,.1f} PB"


def _percent_bar(value: float, width: int = 10) -> str:
    filled = max(0, min(width, int(round(value / (100.0 / width)))))
    return "#" * filled + "." * (width - filled)
 

def compute_rates(current: list[Sample], previous: list[Sample]) -> dict[str, float]:

    prev_by_series = {s.series_key: s for s in previous if s.kind is MetricKind.COUNTER}
    rates: dict[str, float] = {}
    for s in current:
        if s.kind is not MetricKind.COUNTER:
            continue
        prev = prev_by_series.get(s.series_key)
        if prev is None:
            continue
        dt = (s.timestamp - prev.timestamp).total_seconds()
        if dt <= 0:
            continue
        delta = s.value - prev.value
        if delta < 0:  # counter reset -> can't compute a meaningful rate
            continue
        rates[s.series_key] = delta / dt
    return rates


def _display_value(s: Sample, rates: dict[str, float]) -> str:
    """Human-readable value for one sample, given any computed rates."""

    if s.unit == "percent":
        return f"{s.value:5.1f}%  [{_percent_bar(s.value)}]"
    if s.kind is MetricKind.COUNTER:
        rate = rates.get(s.series_key)
        if rate is not None:
            if s.unit == "bytes":
                return f"{human_bytes(rate)}/s"
            return f"{rate:,.0f} {s.unit}/s".strip()
        # No previous cycle yet: show the running total instead of a rate.
        if s.unit == "bytes":
            return f"{human_bytes(s.value)} total"
        return f"{s.value:,.0f} {s.unit} total".strip()
    # Non-percent gauge.
    if s.unit == "bytes":
        return human_bytes(s.value)
    return f"{s.value:,.0f} {s.unit}".strip()


def _label_suffix(s: Sample) -> str:

    if not s.labels:
        return ""
    return " {" + ", ".join(f"{k}={v}" for k, v in s.labels) + "}"


def format_report(
    samples: list[Sample],
    db_path: str,
    rates: Optional[dict[str, float]] = None,
) -> str:
    """Build the grouped monitoring report string."""
    
    rates = rates or {}
    width = 44
    lines: list[str] = []
    lines.append("=" * width)
    lines.append(" Infrastructure Monitoring Report")
    lines.append("=" * width)
    if samples:
        stamp = samples[0].timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        lines.append(f" Taken:    {stamp}")
    lines.append(f" Database: {db_path}")
    lines.append(f" Samples:  {len(samples)}")
 
    # Group by family, preserving a stable order.
    families: dict[str, list[Sample]] = {}
    for s in samples:
        families.setdefault(s.name.split(".")[0], []).append(s)
 
    for family in sorted(families):
        lines.append("-" * width)
        lines.append(f" {family}")
        rows = sorted(families[family], key=lambda s: (s.name, s.labels))
        for s in rows:
            label = _label_suffix(s)
            lines.append(f"   {s.name}{label}")
            lines.append(f"       {_display_value(s, rates)}")
 
    lines.append("=" * width)
    return "\n".join(lines)


def run_cycle(storage: SampleStorage, args, previous: list[Sample]) -> list[Sample]:
    """Collect one snapshot, optionally save it, print the report."""

    samples = collect_samples(disk_paths=args.disk_path or None)
    if not args.no_save:
        storage.save_many(samples)
    rates = compute_rates(samples, previous) if previous else {}
    print(format_report(samples, storage._db_path, rates), flush=True)
    return samples


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="infra-monitor",
        description="Collect labeled system metrics and print a monitoring report.",
    )
    parser.add_argument(
        "--db",
        default="metrics.db",
        help="Path to the SQLite database file (default: metrics.db).",
    )
    parser.add_argument(
        "--disk-path",
        action="append",
        metavar="PATH",
        help="Filesystem path to measure disk usage for. Repeatable. "
        "If omitted, all mounted partitions are measured automatically.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Collect and report without writing samples to the database.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Take a single snapshot and exit, instead of monitoring "
        "continuously until Ctrl+C.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="Seconds between snapshots in continuous mode (default: 10).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
 
    try:
        with SampleStorage(args.db) as storage:
            if args.once:
                run_cycle(storage, args, previous=[])
            else:
                print(
                    f"Monitoring every {args.interval:g}s - press Ctrl+C to stop. "
                    "(counter rates appear after the first interval)",
                    flush=True,
                )
                previous: list[Sample] = []
                while True:
                    previous = run_cycle(storage, args, previous)
                    time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 130
    except Exception as exc:  # noqa: BLE001 - clean top-level failure message
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0
 
 
if __name__ == "__main__":
    raise SystemExit(main())
