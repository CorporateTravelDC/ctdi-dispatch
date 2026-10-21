"""tests/demo/test_profiles_store.py -- demo.profiles' password-gate store.

~~tests/demo/test_profiles_sqlite.py -- 2026-10-16: demo.profiles keeps its
password-gate store in its own SQLite file (DEMO_ACCESS_DB, the demo-state
volume) whenever DISPATCH_DB_BACKEND is not postgres~~ -- renamed and
rewritten 2026-10-20 (operator decision 2026-10-19: only Pi-hole, vnstat and
ACARS Hub touch SQLite; demo_access.db and its mount are gone). The store is
demo_profiles on the DEMO Postgres instance, reached only through
db_backend.demo_pg_conn() under DISPATCH_ROLE=demo + the postgres backend;
anything else is REFUSED (ProfileStoreRefused), never a fallback file.

The "demo instance" here is a sqlite twin of pg_schema_demo's demo_profiles
handed in through a fake demo_pg_conn (test double only; the real pool stays
tripwired by tests/conftest.py and nothing opens a socket).
"""
from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from demo import profiles  # noqa: E402

TWIN = ("CREATE TABLE demo_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, "
        "password_hash TEXT NOT NULL, salt TEXT NOT NULL, window_days INTEGER NOT NULL, "
        "speed REAL NOT NULL DEFAULT 1.0, auto_scale BOOLEAN NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT 1);")


@pytest.fixture
def demo_store(tmp_path, monkeypatch):
    """A demo-role process on the postgres backend, its demo pool faked by a
    sqlite twin under tmp_path."""
    twin = tmp_path / "demo-instance.db"
    c = sqlite3.connect(twin); c.executescript(TWIN); c.commit(); c.close()
    opened = []

    @contextmanager
    def fake_demo_pg_conn():
        conn = sqlite3.connect(twin); conn.row_factory = sqlite3.Row
        opened.append(1)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    monkeypatch.setenv("DISPATCH_DB_BACKEND", "postgres")
    monkeypatch.setenv("DISPATCH_ROLE", "demo")
    monkeypatch.setattr(profiles.db_backend, "backend", lambda: "postgres")
    monkeypatch.setattr(profiles.db_backend, "demo_pg_conn", fake_demo_pg_conn)
    monkeypatch.setattr(profiles, "PBKDF2_ITERATIONS", 1000)   # speed only; same code path
    return twin, opened


def test_store_is_the_demo_instance_only(demo_store):
    assert profiles.store_kind() == "postgres"
    assert profiles._table() == "demo_profiles"  # noqa: SLF001


@pytest.mark.parametrize("backend,role", [("sqlite", "demo"), ("sqlite", ""), ("postgres", "")])
def test_anything_else_is_refused_not_a_sqlite_fallback(monkeypatch, tmp_path, backend, role):
    """2026-10-20: ~~'sqlite' (everything else)~~ -- a sqlite backend, or a
    postgres backend without the demo role, has NO profile store: every
    public function refuses before any connection, and no file is created."""
    cwd = tmp_path / "cwd"; cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(profiles.db_backend, "backend", lambda: backend)
    monkeypatch.setenv("DISPATCH_ROLE", role)

    def boom(*_a, **_k):
        raise AssertionError("a pool was reached from a refused store")
    monkeypatch.setattr(profiles.db_backend, "demo_pg_conn", boom)
    monkeypatch.setattr(profiles.db_backend, "pg_conn", boom)
    with pytest.raises(profiles.ProfileStoreRefused):
        profiles.store_kind()
    for call in (lambda: profiles.create_profile("P", window_days=14, password="x-y-z-12345"),
                 lambda: profiles.list_profiles(), lambda: profiles.authenticate("x"),
                 lambda: profiles.revoke_profile(1)):
        with pytest.raises(profiles.ProfileStoreRefused):
            call()
    assert list(cwd.iterdir()) == [], "nothing written (no fallback file)"


def test_the_sqlite_store_is_gone_from_the_module():
    src = Path(profiles.__file__).read_text()
    import ast
    tree = ast.parse(src)
    imported = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    assert "sqlite3" not in imported
    for gone in ("access_db_path", "DEFAULT_ACCESS_DB", "_SQLITE_SCHEMA"):
        assert not hasattr(profiles, gone), gone


def test_round_trip_create_authenticate_list_revoke(demo_store):
    twin, opened = demo_store
    created = profiles.create_profile("Prospect A", window_days=14, speed=2.0, auto_scale=True)
    assert created["password"] and created["id"] == 1
    ok = profiles.authenticate(created["password"])
    assert ok == {"id": 1, "label": "Prospect A", "window_days": 14, "speed": 2.0, "auto_scale": True}
    assert profiles.authenticate("wrong-" + created["password"]) is None

    second = profiles.create_profile("Prospect B", window_days=56, password="Given-Pass-1")
    assert second["password"] == "Given-Pass-1"
    listed = profiles.list_profiles()
    assert [r["label"] for r in listed] == ["Prospect B", "Prospect A"]
    assert all("password_hash" not in r and "salt" not in r for r in listed)

    assert profiles.revoke_profile(second["id"]) is True
    assert profiles.revoke_profile(second["id"]) is False, "already inactive"
    assert profiles.revoke_profile(999) is False
    assert [r["label"] for r in profiles.list_profiles()] == ["Prospect A"]
    assert [r["label"] for r in profiles.list_profiles(include_inactive=True)] == ["Prospect B", "Prospect A"]
    assert profiles.authenticate("Given-Pass-1") is None, "revoked profiles no longer authenticate"

    token = profiles.issue_session_token(ok)
    assert profiles.verify_session_token(token)["label"] == "Prospect A"
    assert opened, "the demo pool was used"


def test_plaintext_is_never_stored(demo_store):
    twin, _ = demo_store
    created = profiles.create_profile("P", window_days=14, password="Secret-Plain-9")
    assert b"Secret-Plain-9" not in twin.read_bytes()
    assert created["password"] == "Secret-Plain-9"


def test_demo_role_never_reaches_pg_conn(demo_store, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("pg_conn reached from the demo store")
    monkeypatch.setattr(profiles.db_backend, "pg_conn", boom)
    profiles.create_profile("P", window_days=14, password="x-y-z-1234")
    assert profiles.authenticate("x-y-z-1234")["label"] == "P"
