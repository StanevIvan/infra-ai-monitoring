"""Application configuration and secret loading (Phase B).

Twelve-factor style: configuration comes from the *environment*, never from
code, so the same codebase runs locally, in CI, in Docker, and in the cloud
with only environment differences.

``.env`` is the single local store for every secret — PostgreSQL credentials,
API keys, tokens. :func:`load_dotenv` merges it into ``os.environ`` once at
startup, so *any* component can read a secret with ``os.environ["MY_API_KEY"]``
without knowing anything about this module. Real environment variables always
win over ``.env``, which is what lets Docker, CI, and cloud secret managers
override the local file without editing it.

Resolution order (highest priority first):

1. explicit overrides passed by the caller (e.g. a CLI flag)
2. real environment variables
3. ``.env`` (merged into the environment at startup)
4. built-in defaults

Deliberately dependency-free — standard library only.

Recognized variables::

    INFRA_MONITOR_DB_BACKEND    "sqlite" (default) or "postgres"
    INFRA_MONITOR_SQLITE_PATH   path to the SQLite file (default metrics.db)
    DATABASE_URL                PostgreSQL DSN, used when backend=postgres

Any other keys in ``.env`` (API keys, tokens) are loaded into the environment
too and are available via ``os.environ``.

SECURITY: ``.env`` must never be committed. It is listed in .gitignore.
Anything that renders a connection string must go through :func:`redact_dsn`;
``Settings`` redacts itself in ``repr()`` so a stray log line or traceback
cannot expose a password.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, MutableMapping, Optional
from urllib.parse import urlsplit, urlunsplit

SQLITE = "sqlite"
POSTGRES = "postgres"
VALID_BACKENDS = (SQLITE, POSTGRES)

DEFAULT_SQLITE_PATH = "metrics.db"
DOTENV_FILENAME = ".env"

ENV_BACKEND = "INFRA_MONITOR_DB_BACKEND"
ENV_SQLITE_PATH = "INFRA_MONITOR_SQLITE_PATH"
ENV_DATABASE_URL = "DATABASE_URL"


# --------------------------------------------------------------------------
# .env handling
# --------------------------------------------------------------------------
def find_dotenv(start: Optional[Path] = None) -> Optional[Path]:
    """Locate the nearest ``.env`` by walking up from ``start`` (default cwd).

    Searching upward matters in practice: without it, ``.env`` is only found
    when the process happens to be launched from the project root. Running
    ``python -m infra_monitor.cli`` from a subdirectory, from an IDE, or from
    a scheduler would silently miss every secret.

    Returns ``None`` when no ``.env`` exists anywhere up the tree.
    """
    current = Path(start).resolve() if start else Path.cwd().resolve()
    for directory in (current, *current.parents):
        candidate = directory / DOTENV_FILENAME
        if candidate.is_file():
            return candidate
    return None


def parse_dotenv(path: Path) -> dict[str, str]:
    """Parse a minimal ``.env`` file into a dict.

    Supports ``KEY=value``, ``export KEY=value``, ``#`` comments, blank lines,
    and single/double quoted values. Intentionally small — developer
    convenience, not a full dotenv implementation. Returns ``{}`` if the file
    does not exist.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_dotenv(
    path: Optional[Path] = None,
    *,
    override: bool = False,
    environ: Optional[MutableMapping[str, str]] = None,
) -> dict[str, str]:
    """Merge ``.env`` into the process environment. Call once at startup.

    This is what makes ``.env`` the single store for *all* secrets: after this
    runs, any component can read ``os.environ["SOME_API_KEY"]`` regardless of
    whether the value came from the shell or the file.

    Args:
        path: explicit ``.env`` location. If omitted, :func:`find_dotenv`
            searches upward from the current directory.
        override: when False (the default) real environment variables win over
            the file, so Docker/CI/cloud secrets take precedence without
            editing ``.env``.
        environ: target mapping to mutate (defaults to ``os.environ``);
            injectable so tests never touch the real environment.

    Returns:
        The key/value pairs read from the file (``{}`` if none was found).
    """
    target = os.environ if environ is None else environ
    resolved = Path(path) if path is not None else find_dotenv()
    if resolved is None:
        return {}

    values = parse_dotenv(resolved)
    for key, value in values.items():
        if override or key not in target:
            target[key] = value
    return values


def sqlalchemy_url(dsn: str) -> str:
    """Translate a psycopg-native DSN into a SQLAlchemy URL using psycopg 3.

    Why this exists: the application talks to PostgreSQL through psycopg
    directly, which accepts the plain ``postgresql://`` scheme. Alembic,
    however, runs on SQLAlchemy — and SQLAlchemy 2.0 maps a bare
    ``postgresql://`` URL to **psycopg2**, which this project does not
    install. Running a migration would fail with
    ``ModuleNotFoundError: No module named 'psycopg2'``.

    Rather than keep two near-identical URLs in ``.env`` (which would
    inevitably drift), ``DATABASE_URL`` stays in its psycopg-native form and
    Alembic converts it here:

    ``postgresql://u:p@h/db`` -> ``postgresql+psycopg://u:p@h/db``

    A URL that already names a driver (``postgresql+asyncpg://``) is left
    untouched, so this is safe to call unconditionally.
    """
    if not dsn:
        return dsn
    scheme, sep, rest = dsn.partition("://")
    if not sep:
        return dsn
    if "+" in scheme:  # driver already specified - respect it
        return dsn
    if scheme in ("postgres", "postgresql"):
        return f"postgresql+psycopg://{rest}"
    return dsn


def redact_dsn(dsn: Optional[str]) -> str:
    """Return a DSN safe to print, with any password replaced by ``***``.

    The monitoring report prints the active database on every cycle. For
    SQLite that is a harmless file path, but a PostgreSQL DSN embeds
    credentials — printing it raw would leak a password to stdout, terminal
    scrollback, and any log collector downstream.

    ``postgresql://user:secret@host:5432/db`` -> ``postgresql://user:***@host:5432/db``
    """
    if not dsn:
        return ""
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return "***"
    if not parts.hostname:
        # Not a URL-shaped DSN; don't risk echoing something with a secret.
        return "***"

    userinfo = ""
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += ":***"
        userinfo += "@"

    host = parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"

    return urlunsplit((parts.scheme, f"{userinfo}{host}", parts.path, "", ""))


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
@dataclass(frozen=True, repr=False)
class Settings:
    """Resolved application configuration.

    Immutable: settings are read once at startup and never mutated, so no
    code path can change the database out from under a running collector.

    ``repr`` is custom (hence ``repr=False`` above) so the DSN password is
    redacted. The generated dataclass repr would print ``database_url`` in
    full, which leaks credentials into any traceback, debugger output, or
    ``log.debug("settings=%r", settings)`` call.
    """

    db_backend: str = SQLITE
    sqlite_path: str = DEFAULT_SQLITE_PATH
    database_url: Optional[str] = None

    def __post_init__(self) -> None:
        if self.db_backend not in VALID_BACKENDS:
            raise ValueError(
                f"Unknown db_backend {self.db_backend!r}. "
                f"Expected one of: {', '.join(VALID_BACKENDS)}."
            )
        if self.db_backend == POSTGRES and not self.database_url:
            raise ValueError(
                "db_backend='postgres' requires a DSN. Set "
                f"{ENV_DATABASE_URL} in your .env "
                "(e.g. postgresql://user:pass@host:5432/dbname)."
            )

    def __repr__(self) -> str:
        return (
            f"Settings(db_backend={self.db_backend!r}, "
            f"sqlite_path={self.sqlite_path!r}, "
            f"database_url={redact_dsn(self.database_url)!r})"
        )

    @classmethod
    def from_env(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        *,
        db_backend: Optional[str] = None,
        sqlite_path: Optional[str] = None,
        database_url: Optional[str] = None,
    ) -> "Settings":
        """Build Settings from an environment mapping (default ``os.environ``).

        Call :func:`load_dotenv` first if ``.env`` values should be included.
        Keeping the two steps separate makes this function pure and trivially
        testable: pass a plain dict and get deterministic output.

        Explicit keyword arguments (typically CLI flags) win over the
        environment. Passing ``None`` means "not specified" — which is why the
        CLI's argparse defaults are ``None`` rather than real values.
        """
        env = os.environ if environ is None else environ

        def pick(override: Optional[str], key: str, default: Optional[str]):
            if override is not None:
                return override
            value = env.get(key)
            return value if value not in (None, "") else default

        backend = pick(db_backend, ENV_BACKEND, SQLITE)
        return cls(
            db_backend=str(backend).strip().lower(),
            sqlite_path=str(pick(sqlite_path, ENV_SQLITE_PATH, DEFAULT_SQLITE_PATH)),
            database_url=pick(database_url, ENV_DATABASE_URL, None),
        )

    @property
    def display_name(self) -> str:
        """Safe, human-readable description of the configured database."""
        if self.db_backend == SQLITE:
            return self.sqlite_path
        return redact_dsn(self.database_url)
