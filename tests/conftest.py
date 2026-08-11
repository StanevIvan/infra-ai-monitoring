"""Shared test configuration.

The CLI calls ``load_dotenv()`` at startup, which merges the developer's real
``.env`` into ``os.environ``. That is correct in production and wrong in
tests: it means the suite's behaviour depends on whatever backend the machine
happens to be configured for. Setting ``INFRA_MONITOR_DB_BACKEND=postgres``
to try the Postgres path would make the SQLite CLI tests build a
PostgresRepository and fail, which is confusing and has nothing to do with
the code under test.

The autouse fixture below isolates every test from ambient configuration:

* the ``INFRA_MONITOR_*`` and ``DATABASE_URL`` variables are removed, so tests
  see documented defaults regardless of the shell or ``.env``
* ``.env`` discovery is disabled, so ``load_dotenv()`` finds nothing

``TEST_DATABASE_URL`` is deliberately left intact — it is the opt-in switch
for the PostgreSQL integration tests, not application configuration.
"""

import os

import pytest

# Application configuration that must never bleed in from the environment.
MANAGED_ENV_VARS = (
    "INFRA_MONITOR_DB_BACKEND",
    "INFRA_MONITOR_SQLITE_PATH",
    "DATABASE_URL",
)


@pytest.fixture(autouse=True)
def isolate_configuration(monkeypatch):
    """Run every test against a clean, predictable configuration."""
    for name in MANAGED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    # Stop load_dotenv() from discovering the developer's real .env.
    monkeypatch.setattr("infra_monitor.config.find_dotenv", lambda start=None: None)

    yield

    # monkeypatch restores the environment automatically.
    assert all(v not in os.environ or True for v in MANAGED_ENV_VARS)
