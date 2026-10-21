"""tests/poller/test_opensky_registry_pg_retry.py -- 2026-10-20: the bulk
upsert retry in poller.fetchers.opensky_registry caught
sqlite3.OperationalError "locked", which never matches on Postgres (the
platform's only backend since 2026-09-19; platform SQLite retired 2026-10-19).
It now retries on the Postgres lock conflicts only (deadlock, lock_timeout,
serialization failure) and re-raises everything else."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from psycopg import errors as pg_errors

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from poller.fetchers import opensky_registry as osr  # noqa: E402


def test_transient_postgres_lock_errors_are_retried():
    for cls in (pg_errors.DeadlockDetected, pg_errors.LockNotAvailable, pg_errors.SerializationFailure):
        assert osr._is_transient_lock_error(cls("synthetic")), cls  # noqa: SLF001


def test_everything_else_is_not_transient():
    import sqlite3
    for e in (pg_errors.UniqueViolation("x"), pg_errors.UndefinedTable("x"), ValueError("locked"),
              sqlite3.OperationalError("database is locked")):
        assert not osr._is_transient_lock_error(e), e  # noqa: SLF001


def test_module_no_longer_imports_sqlite3():
    tree = ast.parse(Path(osr.__file__).read_text())
    names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    assert "sqlite3" not in names
