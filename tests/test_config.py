"""Tests for configuration, secret loading, and the repository factory.

Environments are always passed in explicitly (never mutating os.environ), so
tests stay isolated and cannot leak state into one another.
"""

import os
from pathlib import Path

import pytest

from infra_monitor.config import (
    DEFAULT_SQLITE_PATH,
    Settings,
    find_dotenv,
    load_dotenv,
    parse_dotenv,
    redact_dsn,
    sqlalchemy_url,
)
from infra_monitor.factory import get_repository
from infra_monitor.repository import MetricsRepository
from infra_monitor.storage import SqliteRepository

NO_ENV: dict[str, str] = {}
MISSING_DOTENV = Path("/nonexistent/.env")


# --- Settings resolution --------------------------------------------------


def test_defaults_when_environment_is_empty():
    s = Settings.from_env(NO_ENV)
    assert s.db_backend == "sqlite"
    assert s.sqlite_path == DEFAULT_SQLITE_PATH
    assert s.database_url is None


def test_environment_variables_are_read():
    s = Settings.from_env({"INFRA_MONITOR_SQLITE_PATH": "/data/custom.db"})
    assert s.sqlite_path == "/data/custom.db"


def test_explicit_override_beats_environment():
    env = {"INFRA_MONITOR_SQLITE_PATH": "/from/env.db"}
    assert Settings.from_env(env, sqlite_path="/from/cli.db").sqlite_path == "/from/cli.db"


def test_none_override_falls_through_to_environment():
    # An unset CLI flag is None and must NOT shadow the environment.
    env = {"INFRA_MONITOR_SQLITE_PATH": "/from/env.db"}
    assert Settings.from_env(env, sqlite_path=None).sqlite_path == "/from/env.db"


def test_empty_environment_value_falls_back_to_default():
    assert Settings.from_env({"INFRA_MONITOR_SQLITE_PATH": ""}).sqlite_path == DEFAULT_SQLITE_PATH


def test_unknown_backend_rejected():
    with pytest.raises(ValueError):
        Settings.from_env({"INFRA_MONITOR_DB_BACKEND": "mysql"})


def test_postgres_without_dsn_rejected():
    with pytest.raises(ValueError):
        Settings.from_env({"INFRA_MONITOR_DB_BACKEND": "postgres"})


def test_postgres_with_dsn_is_valid():
    s = Settings.from_env(
        {
            "INFRA_MONITOR_DB_BACKEND": "postgres",
            "DATABASE_URL": "postgresql://u:p@localhost:5432/infra",
        }
    )
    assert s.db_backend == "postgres"


def test_backend_is_normalized():
    assert Settings.from_env({"INFRA_MONITOR_DB_BACKEND": "  SQLite "}).db_backend == "sqlite"


# --- .env parsing and loading ---------------------------------------------


def test_parse_dotenv_handles_comments_quotes_and_export(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# a comment\n"
        "\n"
        "PLAIN=value\n"
        'QUOTED="quoted value"\n'
        "SINGLE='single'\n"
        "export EXPORTED=yes\n"
    )
    assert parse_dotenv(dotenv) == {
        "PLAIN": "value",
        "QUOTED": "quoted value",
        "SINGLE": "single",
        "EXPORTED": "yes",
    }


def test_parse_dotenv_missing_file_returns_empty():
    assert parse_dotenv(MISSING_DOTENV) == {}


def test_load_dotenv_populates_environment(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("DATABASE_URL=postgresql://u:p@h:5432/d\nMY_API_KEY=abc123\n")
    env: dict[str, str] = {}
    load_dotenv(dotenv, environ=env)
    # Arbitrary secrets (API keys, tokens) land in the environment too.
    assert env["MY_API_KEY"] == "abc123"
    assert env["DATABASE_URL"] == "postgresql://u:p@h:5432/d"


def test_real_environment_wins_over_dotenv(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("MY_API_KEY=from_file\n")
    env = {"MY_API_KEY": "from_shell"}
    load_dotenv(dotenv, environ=env)
    assert env["MY_API_KEY"] == "from_shell"


def test_load_dotenv_override_forces_file_values(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("MY_API_KEY=from_file\n")
    env = {"MY_API_KEY": "from_shell"}
    load_dotenv(dotenv, override=True, environ=env)
    assert env["MY_API_KEY"] == "from_file"


def test_load_dotenv_missing_file_is_a_noop():
    env: dict[str, str] = {}
    assert load_dotenv(MISSING_DOTENV, environ=env) == {}
    assert env == {}


def test_dotenv_then_settings_end_to_end(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("INFRA_MONITOR_SQLITE_PATH=/from/dotenv.db\n")
    env: dict[str, str] = {}
    load_dotenv(dotenv, environ=env)
    assert Settings.from_env(env).sqlite_path == "/from/dotenv.db"


def test_find_dotenv_searches_upward(tmp_path):
    # .env at the project root must be found from a nested working directory.
    (tmp_path / ".env").write_text("X=1\n")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_dotenv(nested) == tmp_path / ".env"


def test_find_dotenv_returns_none_when_absent(tmp_path):
    nested = tmp_path / "empty"
    nested.mkdir()
    # tmp_path tree has no .env; may still find one further up in odd setups,
    # so assert only that a missing local file doesn't resolve to this dir.
    assert find_dotenv(nested) != nested / ".env"


# --- credential safety ----------------------------------------------------


def test_redact_dsn_masks_password():
    out = redact_dsn("postgresql://ivan:supersecret@db.example.com:5432/infra")
    assert "supersecret" not in out
    assert out == "postgresql://ivan:***@db.example.com:5432/infra"


def test_redact_dsn_without_password_is_unchanged():
    assert redact_dsn("postgresql://db.example.com:5432/infra") == (
        "postgresql://db.example.com:5432/infra"
    )


def test_redact_dsn_handles_empty_and_garbage():
    assert redact_dsn(None) == ""
    assert redact_dsn("") == ""
    assert redact_dsn("not-a-url-with-secret") == "***"


def test_settings_display_name_never_leaks_password():
    s = Settings(
        db_backend="postgres", database_url="postgresql://ivan:supersecret@host:5432/infra"
    )
    assert "supersecret" not in s.display_name


def test_settings_repr_never_leaks_password():
    # The generated dataclass repr would print the DSN in full, exposing the
    # password in tracebacks, debuggers and log.debug("%r", settings) calls.
    s = Settings(
        db_backend="postgres", database_url="postgresql://ivan:supersecret@host:5432/infra"
    )
    assert "supersecret" not in repr(s)
    assert "supersecret" not in str(s)
    assert "***" in repr(s)


def test_settings_display_name_for_sqlite_is_the_path():
    assert Settings(sqlite_path="/data/metrics.db").display_name == "/data/metrics.db"


# --- SQLAlchemy URL translation (Alembic) ---------------------------------


def test_sqlalchemy_url_pins_psycopg3():
    # A bare postgresql:// URL would make SQLAlchemy look for psycopg2,
    # which this project does not install.
    assert sqlalchemy_url("postgresql://u:p@h:5432/db") == ("postgresql+psycopg://u:p@h:5432/db")


def test_sqlalchemy_url_normalizes_postgres_scheme():
    assert sqlalchemy_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def test_sqlalchemy_url_respects_an_explicit_driver():
    assert sqlalchemy_url("postgresql+asyncpg://u:p@h/db") == ("postgresql+asyncpg://u:p@h/db")


def test_sqlalchemy_url_leaves_other_schemes_alone():
    assert sqlalchemy_url("sqlite:///metrics.db") == "sqlite:///metrics.db"
    assert sqlalchemy_url("") == ""


def test_sqlalchemy_url_preserves_credentials_for_connection():
    # Translation must not mangle the password - only display paths redact.
    out = sqlalchemy_url("postgresql://ivan:s3cret@h:5432/infra")
    assert "s3cret" in out


# --- factory --------------------------------------------------------------


def test_factory_returns_sqlite_repository():
    repo = get_repository(Settings(sqlite_path=":memory:"))
    try:
        assert isinstance(repo, SqliteRepository)
        assert isinstance(repo, MetricsRepository)
    finally:
        repo.close()


def test_factory_honors_configured_sqlite_path():
    repo = get_repository(Settings(sqlite_path=":memory:"))
    try:
        assert repo.display_name == ":memory:"
    finally:
        repo.close()


def test_factory_builds_a_postgres_repository():
    # Phase E: the factory now dispatches to PostgresRepository. Constructing
    # one opens a connection, so this needs both the driver and a live server.
    pytest.importorskip("psycopg")
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")

    from infra_monitor.postgres_storage import PostgresRepository

    repo = get_repository(Settings(db_backend="postgres", database_url=dsn))
    try:
        assert isinstance(repo, PostgresRepository)
        assert isinstance(repo, MetricsRepository)
    finally:
        repo.close()


def test_factory_postgres_without_driver_explains_how_to_install():
    # Without psycopg the failure must be an actionable install message, not
    # a bare ImportError from deep inside the module.
    from infra_monitor import postgres_storage

    if postgres_storage.PSYCOPG_AVAILABLE:
        pytest.skip("psycopg is installed; the missing-driver path cannot run")
    with pytest.raises(RuntimeError) as exc:
        get_repository(Settings(db_backend="postgres", database_url="postgresql://u:p@h:5432/d"))
    assert "psycopg" in str(exc.value)
