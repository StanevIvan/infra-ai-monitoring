"""Tests for the CLI entrypoint.

collect_samples is mocked so tests don't depend on real system load; main is
driven in-process via main(argv) against a temp on-disk database.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from infra_monitor.cli import (
    build_parser,
    compute_rates,
    format_report,
    human_bytes,
    main,
)
from infra_monitor.models import MetricKind, Sample

T0 = datetime(2026, 7, 10, 9, 0, 0, tzinfo=timezone.utc)


def _snapshot(ts=T0, sent=1000):
    return [
        Sample.create("cpu.percent", 12.5, unit="percent", timestamp=ts),
        Sample.create("mem.used", 8_000_000_000, unit="bytes", timestamp=ts),
        Sample.create("net.bytes_sent", sent, kind=MetricKind.COUNTER,
                      unit="bytes", labels={"interface": "eth0"}, timestamp=ts),
    ]


def test_human_bytes():
    assert human_bytes(512) == "512 B"
    assert human_bytes(1024) == "1.0 KB"
    assert human_bytes(1024 * 1024) == "1.0 MB"


def test_format_report_groups_and_shows_values():
    report = format_report(_snapshot(), "metrics.db")
    assert "Infrastructure Monitoring Report" in report
    assert "12.5%" in report          # gauge percent
    assert "7.5 GB" in report         # gauge bytes (8e9 / 1024^3)
    assert "net.bytes_sent" in report
    assert "interface=eth0" in report  # label rendered
    assert "Samples:  3" in report


def test_compute_rates_from_two_cycles():
    prev = _snapshot(ts=T0, sent=1000)
    curr = _snapshot(ts=T0 + timedelta(seconds=10), sent=3000)
    rates = compute_rates(curr, prev)
    key = "net.bytes_sent{interface=eth0}"
    assert rates[key] == 200.0  # (3000-1000)/10s


def test_compute_rates_ignores_counter_reset():
    prev = _snapshot(ts=T0, sent=5000)
    curr = _snapshot(ts=T0 + timedelta(seconds=10), sent=100)  # reboot -> reset
    assert compute_rates(curr, prev) == {}


def test_main_once_saves_and_prints(tmp_path, capsys):
    db = tmp_path / "metrics.db"
    with patch("infra_monitor.cli.collect_samples", return_value=_snapshot()):
        code = main(["--db", str(db), "--once"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Infrastructure Monitoring Report" in out
    assert db.exists()


def test_main_no_save_does_not_create_db(tmp_path, capsys):
    db = tmp_path / "metrics.db"
    with patch("infra_monitor.cli.collect_samples", return_value=_snapshot()):
        code = main(["--db", str(db), "--once", "--no-save"])
    assert code == 0
    assert "12.5%" in capsys.readouterr().out


def test_continuous_loops_until_ctrlc(tmp_path, capsys):
    db = tmp_path / "metrics.db"
    calls = {"n": 0}

    def fake_sleep(_):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise KeyboardInterrupt

    with patch("infra_monitor.cli.collect_samples", return_value=_snapshot()), \
         patch("infra_monitor.cli.time.sleep", side_effect=fake_sleep):
        code = main(["--db", str(db), "--interval", "1"])
    out = capsys.readouterr().out
    assert code == 130
    assert out.count("Infrastructure Monitoring Report") >= 3
    assert "Stopped." in out


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.db == "metrics.db"
    assert args.once is False
    assert args.no_save is False
    assert args.interval == 10.0
    assert args.disk_path is None