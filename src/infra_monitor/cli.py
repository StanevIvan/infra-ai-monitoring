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

Common options:

    python -m infra_monitor.cli --limit 10          # show last 10 snapshots
    python -m infra_monitor.cli --no-save           # don't write to the db
    python -m infra_monitor.cli --watch --interval 5  # keep collecting
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# --- Make direct execution work (python src/infra_monitor/cli.py) ----------
# When run as a plain script, the "infra_monitor" package isn't on sys.path,
# so the imports below would fail. Add the "src" directory (this file's
# grandparent) to sys.path so the package resolves either way.
try:
    from infra_monitor.collector import collect_metric
    from infra_monitor.models import Metric
    from infra_monitor.storage import MetricsStorage
except ModuleNotFoundError:  # pragma: no cover - exercised only on direct run
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from infra_monitor.collector import collect_metric
    from infra_monitor.models import Metric
    from infra_monitor.storage import MetricsStorage


def default_disk_path() -> str:
    """Pick a sensible disk path to check for the current OS.

    psutil.disk_usage("/") raises on Windows, so default to the system drive
    there (typically C:\\) and to the filesystem root elsewhere.
    """
    if os.name == "nt":
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


def _fmt_row(label: str, value: float) -> str:
    """Format one metric line with a tiny inline bar for quick scanning."""
    filled = int(round(value / 10))  # 0..10 blocks
    filled = max(0, min(10, filled))
    bar = "#" * filled + "." * (10 - filled)
    return f"  {label:<5} {value:5.1f}%  [{bar}]"


def format_report(current: Metric, recent: list[Metric], db_path: str) -> str:
    """Build the human-readable monitoring report string."""
    lines: list[str] = []
    lines.append("=" * 44)
    lines.append(" Infrastructure Monitoring Report")
    lines.append("=" * 44)
    lines.append(
        f" Taken:    {current.timestamp.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}"
    )
    lines.append(f" Database: {db_path}")
    lines.append("-" * 44)
    lines.append(" Current snapshot:")
    lines.append(_fmt_row("CPU", current.cpu_percent))
    lines.append(_fmt_row("MEM", current.mem_percent))
    lines.append(_fmt_row("DISK", current.disk_percent))

    if len(recent) > 1:
        lines.append("-" * 44)
        lines.append(f" Recent history (newest first, up to {len(recent)}):")
        lines.append(f"   {'time':<19}  {'cpu':>6} {'mem':>6} {'disk':>6}")
        for m in recent:
            ts = m.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            lines.append(
                f"   {ts:<19}  {m.cpu_percent:5.1f}% {m.mem_percent:5.1f}% {m.disk_percent:5.1f}%"
            )

    lines.append("=" * 44)
    return "\n".join(lines)


def run_once(storage: MetricsStorage, args: argparse.Namespace) -> None:
    """Collect one snapshot, optionally save it, and print the report."""
    metric = collect_metric(disk_path=args.disk_path)
    if not args.no_save:
        storage.save(metric)
    recent = storage.get_recent(limit=args.limit)
    # If we didn't save, the current snapshot may not be in `recent`; make sure
    # the report still reflects what we just collected.
    if args.no_save and (not recent or recent[0].timestamp != metric.timestamp):
        recent = [metric, *recent][: args.limit]
    print(format_report(metric, recent, storage._db_path), flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="infra-monitor",
        description="Collect system metrics and print a monitoring report.",
    )
    parser.add_argument(
        "--db",
        default="metrics.db",
        help="Path to the SQLite database file (default: metrics.db).",
    )
    parser.add_argument(
        "--disk-path",
        default=default_disk_path(),
        help="Filesystem path to measure disk usage for "
        f"(default: {default_disk_path()!r}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="How many recent snapshots to show in the report (default: 5).",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Collect and report without writing the snapshot to the database.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep collecting on a fixed interval until interrupted (Ctrl+C).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="Seconds between snapshots when using --watch (default: 10).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        with MetricsStorage(args.db) as storage:
            if args.watch:
                print(
                    f"Watching every {args.interval:g}s - press Ctrl+C to stop.",
                    flush=True,
                )
                while True:
                    run_once(storage, args)
                    time.sleep(args.interval)
            else:
                run_once(storage, args)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
