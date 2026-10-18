"""tests/demo/test_profiles_sqlite.py -- 2026-10-16: demo.profiles keeps its
password-gate store in its own SQLite file (DEMO_ACCESS_DB, the demo-state
volume) whenever DISPATCH_DB_BACKEND is not postgres, so the public demo's
login works with no path to production. The schema is the pre-2026-09-20
`profiles` table byte-for-byte, so the file already in
/var/lib/corporatetraveldc-demo-state is read as-is. No network, no
Postgres (conftest's tripwire would fail the test if anything reached it).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from demo import profiles  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "sqlite")
    path = tmp_path / "demo-state" / "demo_access.db"
    monkeypatch.setenv("DEMO_ACCESS_DB", str(path))
    monkeypatch.setattr(profiles, "PBKDF2_ITERATIONS", 1000)   # speed only; same code path
    return path


def test_store_kind_follows_backend_and_role(store, monkeypatch):
    """2026-10-16 split: Postgres here means the DEMO instance, selected by
    DISPATCH_ROLE=demo on a postgres backend; the backend alone never does."""
    assert profiles.store_kind() == "sqlite"
    assert profiles._table() == "profiles"  # noqa: SLF001
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "postgres")
    monkeypatch.setattr(profiles.db_backend, "backend", lambda: "postgres")
    assert profiles.store_kind() == "sqlite", "postgres backend without the demo role stays on the sqlite file"
    monkeypatch.setenv("DISPATCH_ROLE", "demo")
    assert profiles.store_kind() == "postgres"
    assert profiles._table() == "demo_profiles"  # noqa: SLF001


def test_demo_role_uses_the_demo_pool_and_never_pg_conn(store, monkeypatch, tmp_path):
    """Under DISPATCH_ROLE=demo the store is db_backend.demo_pg_conn (the
    demo instance). A sqlite twin of pg_schema_demo's demo_profiles stands
    in; pg_conn stays tripwired by conftest and is never reached."""
    import sqlite3
    from contextlib import contextmanager

    twin = tmp_path / "demo-instance.db"
    c = sqlite3.connect(twin)
    c.executescript(
        "CREATE TABLE demo_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, password_hash TEXT NOT NULL, "
        "salt TEXT NOT NULL, window_days INTEGER NOT NULL, speed REAL NOT NULL DEFAULT 1.0, auto_scale BOOLEAN NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT 1);")
    c.commit(); c.close()

    @contextmanager
    def fake_demo_pg_conn():
        conn = sqlite3.connect(twin); conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    monkeypatch.setenv("DISPATCH_DB_BACKEND", "postgres")
    monkeypatch.setenv("DISPATCH_ROLE", "demo")
    monkeypatch.setattr(profiles.db_backend, "backend", lambda: "postgres")
    monkeypatch.setattr(profiles.db_backend, "demo_pg_conn", fake_demo_pg_conn)
    created = profiles.create_profile("Demo P", window_days=14, password="demo-pass-1234")
    assert profiles.authenticate("demo-pass-1234")["label"] == "Demo P"
    assert profiles.revoke_profile(created["id"]) is True
    assert profiles.authenticate("demo-pass-1234") is None
    assert not store.exists(), "the sqlite file was never touched under the demo role"


def test_round_trip_create_authenticate_list_revoke(store):
    created = profiles.create_profile("Prospect A", window_days=14, speed=2.0, auto_scale=True)
    assert created["password"] and created["id"] == 1
    assert store.exists(), "the store is created under DEMO_ACCESS_DB (parent dirs made)"

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


def test_plaintext_is_never_stored(store):
    created = profiles.create_profile("P", window_days=14, password="Secret-Plain-9")
    blob = store.read_bytes()
    assert b"Secret-Plain-9" not in blob
    assert created["password"] == "Secret-Plain-9"


def test_reads_the_legacy_file_as_is(store):
    """A demo_access.db written by the pre-2026-09-20 module (INTEGER
    booleans, `profiles` table) authenticates without any migration."""
    store.parent.mkdir(parents=True)
    c = sqlite3.connect(store)
    c.executescript(profiles._SQLITE_SCHEMA)  # noqa: SLF001
    salt = bytes.fromhex("00112233445566778899aabbccddeeff")
    c.execute(
        "INSERT INTO profiles (label, password_hash, salt, window_days, speed, auto_scale, created_at, active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("Legacy", profiles._hash("legacy-pass", salt), salt.hex(), 84, 1.0, 1, "2026-08-02T04:00:48+00:00", 1),  # noqa: SLF001
    )
    c.execute(
        "INSERT INTO profiles (label, password_hash, salt, window_days, speed, auto_scale, created_at, active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("Gone", profiles._hash("gone-pass", salt), salt.hex(), 14, 1.0, 0, "2026-08-01T00:00:00+00:00", 0),  # noqa: SLF001
    )
    c.commit(); c.close()
    got = profiles.authenticate("legacy-pass")
    assert got and got["label"] == "Legacy" and got["auto_scale"] is True
    assert profiles.authenticate("gone-pass") is None
    assert [r["label"] for r in profiles.list_profiles()] == ["Legacy"]


def test_sqlite_path_never_touches_db_backend_pg_conn(store, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("pg_conn reached from the sqlite store")
    monkeypatch.setattr(profiles.db_backend, "pg_conn", boom)
    profiles.create_profile("P", window_days=14, password="x-y-z-1234")
    assert profiles.authenticate("x-y-z-1234")["label"] == "P"


def test_default_path_is_the_demo_state_volume(monkeypatch):
    monkeypatch.delenv("DEMO_ACCESS_DB", raising=False)
    assert profiles.access_db_path() == "/var/lib/corporatetraveldc-demo-state/demo_access.db"
