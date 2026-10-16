"""tests/common/test_db_backend_demo_isolation.py -- 2026-10-16 demo/production
database isolation guard in common.db_backend.

Verified live 2026-10-16 12:13Z: one Postgres instance, one database, one
superuser role; corporatetraveldc-demo-api pinned DISPATCH_DB_BACKEND=sqlite
yet src/demo/profiles.py went through pg_conn() straight at production and
failed only for lack of a password. The guard: _pg_conninfo()/pg_conn()
refuse under a non-postgres backend, and under DISPATCH_DEMO_ISOLATION=1
refuse the production database/role pair. Nothing here opens a socket.

tests/conftest.py replaces pg_conn/_pg_conninfo with a tripwire per test;
the real functions are captured at import (before any fixture runs) and
exercised directly. Both refusals happen before _get_pool(), which stays
tripwired -- reaching it would fail the test loudly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from common import db_backend  # noqa: E402

REAL_PG_CONN = db_backend.pg_conn
REAL_PG_CONNINFO = db_backend._pg_conninfo  # noqa: SLF001
REAL_BACKEND = db_backend.backend


@pytest.fixture
def real(monkeypatch):
    """Undo the conftest tripwire for the two guarded entry points and the
    backend() reader (conftest pins it to a lambda returning 'sqlite')."""
    monkeypatch.setattr(db_backend, "pg_conn", REAL_PG_CONN)
    monkeypatch.setattr(db_backend, "_pg_conninfo", REAL_PG_CONNINFO)
    monkeypatch.setattr(db_backend, "backend", REAL_BACKEND)
    monkeypatch.delenv("DISPATCH_DEMO_ISOLATION", raising=False)
    monkeypatch.delenv("DISPATCH_PG_PASSWORD", raising=False)
    return db_backend


def test_pg_conn_refuses_under_sqlite_backend(real, monkeypatch):
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "sqlite")
    with pytest.raises(db_backend.PostgresRefused, match="DISPATCH_DB_BACKEND='sqlite'"):
        with real.pg_conn():
            pass


def test_pg_conninfo_refuses_under_sqlite_backend(real, monkeypatch):
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "sqlite")
    with pytest.raises(db_backend.PostgresRefused):
        real._pg_conninfo()  # noqa: SLF001


def test_backend_value_is_normalised(real, monkeypatch):
    # " Postgres " is the postgres backend; "sqlite3" / "" are not.
    monkeypatch.setenv("DISPATCH_PG_HOST", "127.0.0.1")
    monkeypatch.setenv("DISPATCH_PG_DB", "ctdc_demo")
    monkeypatch.setenv("DISPATCH_PG_USER", "demo")
    monkeypatch.setenv("DISPATCH_DB_BACKEND", " Postgres ")
    assert "dbname=ctdc_demo" in real._pg_conninfo()  # noqa: SLF001
    for bad in ("sqlite3", ""):
        monkeypatch.setenv("DISPATCH_DB_BACKEND", bad)
        with pytest.raises(db_backend.PostgresRefused):
            real._pg_conninfo()  # noqa: SLF001


@pytest.mark.parametrize("flag", ["1", "true", "YES", "on"])
def test_isolation_flag_refuses_the_production_pair(real, monkeypatch, flag):
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "postgres")
    monkeypatch.setenv("DISPATCH_PG_HOST", "127.0.0.1")
    monkeypatch.setenv("DISPATCH_PG_DB", db_backend.PRODUCTION_PG_DB)
    monkeypatch.setenv("DISPATCH_PG_USER", db_backend.PRODUCTION_PG_USER)
    monkeypatch.setenv("DISPATCH_DEMO_ISOLATION", flag)
    with pytest.raises(db_backend.PostgresRefused, match="DISPATCH_DEMO_ISOLATION=1"):
        real._pg_conninfo()  # noqa: SLF001
    with pytest.raises(db_backend.PostgresRefused, match="DISPATCH_DEMO_ISOLATION=1"):
        with real.pg_conn():
            pass


def test_isolation_flag_refuses_either_half_of_the_pair(real, monkeypatch):
    """A demo role on the production database is still production data; the
    production role on a demo database is still the superuser."""
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "postgres")
    monkeypatch.setenv("DISPATCH_PG_HOST", "127.0.0.1")
    monkeypatch.setenv("DISPATCH_DEMO_ISOLATION", "1")
    monkeypatch.setenv("DISPATCH_PG_DB", db_backend.PRODUCTION_PG_DB)
    monkeypatch.setenv("DISPATCH_PG_USER", "demo")
    with pytest.raises(db_backend.PostgresRefused):
        real._pg_conninfo()  # noqa: SLF001
    monkeypatch.setenv("DISPATCH_PG_DB", "ctdc_demo")
    monkeypatch.setenv("DISPATCH_PG_USER", db_backend.PRODUCTION_PG_USER)
    with pytest.raises(db_backend.PostgresRefused):
        real._pg_conninfo()  # noqa: SLF001


def test_isolation_flag_allows_a_demo_pair(real, monkeypatch):
    """Conninfo for a demo-owned database/role is built (no socket opened)."""
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "postgres")
    monkeypatch.setenv("DISPATCH_PG_HOST", "127.0.0.1")
    monkeypatch.setenv("DISPATCH_DEMO_ISOLATION", "1")
    monkeypatch.setenv("DISPATCH_PG_DB", "ctdc_demo")
    monkeypatch.setenv("DISPATCH_PG_USER", "demo")
    ci = real._pg_conninfo()  # noqa: SLF001
    assert "dbname=ctdc_demo" in ci and "user=demo" in ci
    assert "password=" not in ci


def test_production_untouched_without_the_flag(real, monkeypatch):
    """The production containers never set the flag; the production pair
    under the postgres backend still resolves (this is what every
    production process does today)."""
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "postgres")
    monkeypatch.setenv("DISPATCH_PG_HOST", "127.0.0.1")
    monkeypatch.setenv("DISPATCH_PG_DB", db_backend.PRODUCTION_PG_DB)
    monkeypatch.setenv("DISPATCH_PG_USER", db_backend.PRODUCTION_PG_USER)
    ci = real._pg_conninfo()  # noqa: SLF001
    assert f"dbname={db_backend.PRODUCTION_PG_DB}" in ci


def test_refusal_message_never_carries_the_password(real, monkeypatch):
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "postgres")
    monkeypatch.setenv("DISPATCH_PG_HOST", "127.0.0.1")
    monkeypatch.setenv("DISPATCH_DEMO_ISOLATION", "1")
    monkeypatch.setenv("DISPATCH_PG_PASSWORD", "not-a-real-password-sentinel")
    with pytest.raises(db_backend.PostgresRefused) as ei:
        real._pg_conninfo()  # noqa: SLF001
    assert "not-a-real-password-sentinel" not in str(ei.value)


def test_stale_sqlite_comment_is_gone():
    src = (Path(__file__).resolve().parents[2] / "src/common/db_backend.py").read_text()
    assert "stays sqlite in every env file). Before any" not in src
