# Infrastructure AI Monitoring Platform

A small infrastructure monitoring system, built incrementally, that collects
system metrics, stores them, and (starting later in the roadmap) uses simple
ML techniques to flag anomalies automatically.

This project is being built in public as part of a structured 6-month
learning plan covering Python engineering practices, containerization,
CI/CD, cloud infrastructure, observability, and applied ML.

## Current status: Month 1 — foundations

At this stage the project can:
- Collect a broad snapshot of system metrics from the local machine via `psutil`:
  CPU (overall plus a user/system/idle/iowait breakdown), memory and swap,
  per-mount disk usage, disk I/O, and per-interface network I/O.
- Model each measurement with a **narrow, labeled** data model (the same shape
  used by time-series systems like Prometheus): every reading is a `Sample`
  with a name, value, kind (gauge vs counter), unit, and a set of string labels.
- Persist samples to a local SQLite database (one row per measurement).
- Print a grouped monitoring report from a CLI, computing per-second **rates**
  for counter metrics (network/disk I/O) from consecutive snapshots.
- Validate that values are sane (percentages stay 0–100, counters stay non-negative).

Everything is covered by unit tests, and the database access layer is
isolated behind a single class so it can be swapped for PostgreSQL later
without touching the rest of the codebase.

### Gauges vs counters

Two kinds of metric are collected, and the distinction matters:

- **Gauges** are point-in-time values meaningful on their own (`cpu.percent`,
  `mem.used`). The report shows them directly.
- **Counters** are monotonic totals since boot (`net.bytes_sent`); the
  interesting quantity is their *rate of change*. The CLI computes a
  per-second rate from the previous snapshot, so counter rates appear from the
  second cycle onward.

## Project structure

```
infra-ai-monitoring/
├── src/
│   └── infra_monitor/
│       ├── __init__.py
│       ├── models.py      # Sample data model + MetricKind (gauge/counter)
│       ├── collector.py   # Reads system metrics via psutil -> list[Sample]
│       ├── storage.py     # SQLite persistence layer (samples table)
│       └── cli.py         # Command-line entrypoint + report renderer
├── tests/
│   ├── test_models.py
│   ├── test_collector.py
│   ├── test_storage.py
│   └── test_cli.py
├── pyproject.toml
└── README.md
```

## Getting started

Requires Python 3.11+.

```bash
# clone and enter the repo
git clone https://github.com/StanevIvan/infra-ai-monitoring.git
cd infra-ai-monitoring

# create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate      # on Windows: .venv\Scripts\activate

# install the project in editable mode, with dev dependencies
pip install -e ".[dev]"
```

## Usage

Run the monitor from the command line. By default it collects continuously,
printing a fresh report every `--interval` seconds until you stop it with
Ctrl+C:

```bash
python -m infra_monitor.cli                 # monitor until Ctrl+C
python -m infra_monitor.cli --interval 5    # report every 5 seconds
python -m infra_monitor.cli --once          # a single snapshot, then exit
python -m infra_monitor.cli --no-save       # report without writing to the db
python -m infra_monitor.cli --disk-path /   # only measure these mount(s)
```

After `pip install -e .` the same tool is available as the `infra-monitor`
command. On Windows, disk usage is measured across all mounted partitions by
default, so no path argument is needed.

Programmatic use:

```python
from infra_monitor.collector import collect_samples
from infra_monitor.storage import SampleStorage

with SampleStorage("metrics.db") as storage:
    samples = collect_samples()
    storage.save_many(samples)
    for s in storage.get_recent(10):
        print(s.series_key, s.value, s.unit)
```

## Running tests

```bash
pytest
```

Tests use an in-memory SQLite database and mock `psutil`, so they run fast
and never depend on the actual state of your machine or touch a real
database file.

## Roadmap

- **Month 2** — PostgreSQL + Docker Compose, FastAPI service exposing
  ingest/query endpoints
- **Month 3** — CI/CD (GitHub Actions), deployed to the cloud via Terraform
- **Month 4** — Prometheus + Grafana dashboards, anomaly detection on
  metrics (rolling z-score / Isolation Forest), alerting
- **Month 5** — Kubernetes deployment option, architecture diagrams,
  documentation polish
- **Month 6** — Stabilization and portfolio-readiness

## Why this project exists

Built as a bridge between enterprise infrastructure support experience
(z/OS, Unix System Services, OpenSSH) and modern Python/DevOps practices —
applying real troubleshooting instincts to a system designed from scratch,
with production-style engineering discipline (tests, docs, CI/CD) from day one.