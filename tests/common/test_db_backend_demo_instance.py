"""tests/common/test_db_backend_demo_instance.py -- 2026-10-16 demo/production
database split: the demo instance's accessor (demo_pg_conn / _demo_pg_conninfo
/ role_conn) and every refusal, in common.db_backend.

Env var NAMES only; every value here is synthetic. Nothing opens a socket:
each refusal fires before a pool exists, and the pool builders stay
tripwired by tests/conftest.py (reaching one fails the test loudly).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from common import db_backend  # noqa: E402

REAL = {name: getattr(db_backend, name) for name in
        ("pg_conn", "_pg_conninfo", "demo_pg_conn", "_demo_pg_conninfo", "role_conn", "backend")}


@pytest.fixture
def real(monkeypatch):
    for name, fn in REAL.items():
        monkeypatch.setattr(db_backend, name, fn)
    for var in ("DISPATCH_DEMO_ISOLATION", "DISPATCH_ROLE", "DISPATCH_PG_PASSWORD", "DEMO_PG_PASSWORD",
                "DEMO_PG_HOST", "DEMO_PG_PORT", "DEMO_PG_DB", "DEMO_PG_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "postgres")
    monkeypatch.setenv("DISPATCH_PG_HOST", "127.0.0.1")
    monkeypatch.setenv("DISPATCH_PG_DB", db_backend.PRODUCTION_PG_DB)
    monkeypatch.setenv("DISPATCH_PG_USER", db_backend.PRODUCTION_PG_USER)
    return db_backend


def test_demo_isolation_error_is_a_postgres_refused():
    assert issubclass(db_backend.DemoIsolationError, db_backend.PostgresRefused)


def test_pg_conn_refuses_under_the_demo_role(real, monkeypatch):
    monkeypatch.setenv("DISPATCH_ROLE", "demo")
    with pytest.raises(db_backend.DemoIsolationError, match="DISPATCH_ROLE=demo"):
        with real.pg_conn():
            pass


def test_pg_conninfo_refuses_the_demo_db_or_roles(real, monkeypatch):
    monkeypatch.setenv("DISPATCH_PG_DB", db_backend.DEMO_PG_DB)
    with pytest.raises(db_backend.DemoIsolationError):
        real._pg_conninfo()  # noqa: SLF001
    monkeypatch.setenv("DISPATCH_PG_DB", db_backend.PRODUCTION_PG_DB)
    for user in (db_backend.DEMO_PG_USER, db_backend.DEMO_PG_OWNER):
        monkeypatch.setenv("DISPATCH_PG_USER", user)
        with pytest.raises(db_backend.DemoIsolationError):
            real._pg_conninfo()  # noqa: SLF001


def test_production_conninfo_still_builds_without_the_role(real):
    ci = real._pg_conninfo()  # noqa: SLF001
    assert f"dbname={db_backend.PRODUCTION_PG_DB}" in ci and f"user={db_backend.PRODUCTION_PG_USER}" in ci


def test_demo_conninfo_defaults_and_host_fallback(real, monkeypatch, tmp_path):
    """DEMO_PG_HOST defaults to the socket path; on a host without it the
    layer falls back to 127.0.0.1:5433 (the quadlet's publish); DEMO_PG_PORT
    set explicitly always wins."""
    ci = real._demo_pg_conninfo()  # noqa: SLF001
    assert "host=127.0.0.1" in ci and "port=5433" in ci
    assert f"dbname={db_backend.DEMO_PG_DB}" in ci and f"user={db_backend.DEMO_PG_USER}" in ci
    assert "password=" not in ci
    sock = tmp_path / "postgresql-demo"; sock.mkdir()
    monkeypatch.setenv("DEMO_PG_HOST", str(sock))
    ci = real._demo_pg_conninfo()  # noqa: SLF001
    assert f"host={sock}" in ci and "port=5432" in ci
    monkeypatch.setenv("DEMO_PG_PORT", "6543")
    assert "port=6543" in real._demo_pg_conninfo()  # noqa: SLF001


def test_demo_conninfo_refuses_the_production_db_or_role(real, monkeypatch):
    monkeypatch.setenv("DEMO_PG_DB", db_backend.PRODUCTION_PG_DB)
    with pytest.raises(db_backend.DemoIsolationError, match="production database/role"):
        real._demo_pg_conninfo()  # noqa: SLF001
    monkeypatch.delenv("DEMO_PG_DB")
    monkeypatch.setenv("DEMO_PG_USER", db_backend.PRODUCTION_PG_USER)
    with pytest.raises(db_backend.DemoIsolationError):
        real._demo_pg_conninfo()  # noqa: SLF001
    with pytest.raises(db_backend.DemoIsolationError):
        with real.demo_pg_conn():
            pass


def test_demo_conninfo_refuses_under_a_non_postgres_backend(real, monkeypatch):
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "sqlite")
    with pytest.raises(db_backend.PostgresRefused, match="pinned off"):
        real._demo_pg_conninfo()  # noqa: SLF001
    with pytest.raises(db_backend.PostgresRefused):
        with real.demo_pg_conn():
            pass


def test_demo_conninfo_never_carries_the_password_in_a_refusal(real, monkeypatch):
    monkeypatch.setenv("DEMO_PG_PASSWORD", "synthetic-demo-sentinel")
    monkeypatch.setenv("DEMO_PG_USER", db_backend.PRODUCTION_PG_USER)
    with pytest.raises(db_backend.DemoIsolationError) as ei:
        real._demo_pg_conninfo()  # noqa: SLF001
    assert "synthetic-demo-sentinel" not in str(ei.value)


def test_role_conn_routes_by_dispatch_role(real, monkeypatch):
    calls = []

    class _CM:
        def __init__(self, tag): self.tag = tag
        def __enter__(self): calls.append(self.tag); return self.tag
        def __exit__(self, *a): return False

    monkeypatch.setattr(db_backend, "pg_conn", lambda: _CM("production"))
    monkeypatch.setattr(db_backend, "demo_pg_conn", lambda: _CM("demo"))
    with real.role_conn() as c:
        assert c == "production"
    monkeypatch.setenv("DISPATCH_ROLE", "demo")
    with real.role_conn() as c:
        assert c == "demo"
    assert calls == ["production", "demo"]


def test_common_db_conn_goes_through_role_conn(monkeypatch):
    """common.db.conn() names only role_conn -- the two pools are chosen in
    db_backend, so the import boundary test can hold that common.db names
    neither accessor."""
    from common import db
    seen = []

    class _CM:
        def __enter__(self): seen.append("role_conn"); return "c"
        def __exit__(self, *a): return False

    monkeypatch.setattr(db_backend, "backend", lambda: "postgres")
    monkeypatch.setattr(db_backend, "role_conn", lambda: _CM())
    with db.conn() as c:
        assert c == "c"
    assert seen == ["role_conn"]


def test_isolation_flag_and_role_together(real, monkeypatch):
    """The relay-2 demo shape: role demo + isolation flag + demo pair -- the
    demo conninfo builds, the production one is refused twice over."""
    monkeypatch.setenv("DISPATCH_ROLE", "demo")
    monkeypatch.setenv("DISPATCH_DEMO_ISOLATION", "1")
    assert f"dbname={db_backend.DEMO_PG_DB}" in real._demo_pg_conninfo()  # noqa: SLF001
    with pytest.raises(db_backend.PostgresRefused):
        real._pg_conninfo()  # noqa: SLF001
    with pytest.raises(db_backend.DemoIsolationError):
        with real.pg_conn():
            pass
