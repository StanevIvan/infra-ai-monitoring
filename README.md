# Infrastructure AI Monitoring Platform

A small infrastructure monitoring system, built incrementally, that collects
system metrics, stores them, and (starting later in the roadmap) uses simple
ML techniques to flag anomalies automatically.

This project is being built in public as part of a structured 6-month
learning plan covering Python engineering practices, containerization,
CI/CD, cloud infrastructure, observability, and applied ML.

## Current status: Month 1 — foundations

At this stage the project can:
- Collect a snapshot of CPU, memory, and disk usage from the local machine (`psutil`)
- Persist snapshots to a local SQLite database
- Retrieve the most recent snapshots
- Validate that metric values are sane (0–100%)

Everything is covered by unit tests, and the database access layer is
isolated behind a single class so it can be swapped for PostgreSQL later
without touching the rest of the codebase.

## Project structure

```
infra-ai-monitoring/
├── src/
│   └── infra_monitor/
│       ├── __init__.py
│       ├── models.py      # Metric data model
│       ├── collector.py   # Reads system metrics via psutil
│       └── storage.py     # SQLite persistence layer
├── tests/
│   ├── test_collector.py
│   └── test_storage.py
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

```python
from infra_monitor.collector import collect_metric
from infra_monitor.storage import MetricsStorage

with MetricsStorage("metrics.db") as storage:
    metric = collect_metric()
    storage.save(metric)
    print(storage.get_recent(5))
```

Or run it as a quick one-off collection loop (a proper CLI entrypoint and
scheduler will be added once the FastAPI service lands in Month 2):

```bash
python3 -c "
import time
from infra_monitor.collector import collect_metric
from infra_monitor.storage import MetricsStorage

with MetricsStorage('metrics.db') as storage:
    while True:
        m = collect_metric()
        storage.save(m)
        print(m)
        time.sleep(10)
"
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
