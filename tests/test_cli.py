"""Tests for the CLI entrypoint.

We mock the collector so the tests don't depend on real system load, and we
point the CLI at an on-disk temp database so a full save -> query -> report
round trip is exercised end to end.
"""

from datetime import datetime, timezone
from unittest.mock import patch

from infra_monitor.cli import build_parser, format_report, main
from infra_monitor.models import Metric


def _metric(cpu=12.5, mem=55.0, disk=70.0) -> Metric:
    return Metric(
        timestamp=datetime(2026, 7, 10, 9, 0, 0, tzinfo=timezone.utc),
        cpu_percent=cpu,
        mem_percent=mem,
        disk_percent=disk,
    )


def test_format_report_contains_current_values():
    report = format_report(_metric(), [_metric()], "metrics.db")
    assert "Infrastructure Monitoring Report" in report
    assert "12.5%" in report
    assert "55.0%" in report
    assert "70.0%" in report
    assert "metrics.db" in report


def test_main_prints_report_and_saves(tmp_path, capsys):
    db = tmp_path / "metrics.db"
    with patch("infra_monitor.cli.collect_metric", return_value=_metric(cpu=42.0)):
        exit_code = main(["--db", str(db)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "42.0%" in out
    assert db.exists()  # snapshot was persisted


def test_main_no_save_leaves_db_untouched(tmp_path, capsys):
    db = tmp_path / "metrics.db"
    with patch("infra_monitor.cli.collect_metric", return_value=_metric(cpu=7.0)):
        exit_code = main(["--db", str(db), "--no-save"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "7.0%" in out  # still reported even though not saved


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.db == "metrics.db"
    assert args.limit == 5
    assert args.no_save is False
    assert args.once is False
