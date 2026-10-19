"""tests/demo/test_demo_api_source.py -- 2026-10-16 demo/production database
split: src/demo/demo_api.py reads the sovereign SQLite file by default and
the demo instance under DEMO_SOURCE=postgres, with the same anchor /
virtual-timestamp logic and name-keyed rows on both. The "demo instance" is
a sqlite twin handed in through a fake common.db_backend.demo_pg_conn (the
real one stays tripwired); nothing opens a socket.
"""
from __future__ import annotations

import sqlite3
import sys
import zlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from demo import demo_api  # noqa: E402

TWIN = """
CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT NOT NULL, captured_at TEXT NOT NULL,
    payload BLOB NOT NULL, payload_hash TEXT, compressed BOOLEAN NOT NULL DEFAULT 1, synthetic BOOLEAN NOT NULL DEFAULT 0);
CREATE TABLE brief_archive (id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT NOT NULL, brief_type TEXT NOT NULL,
    content TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'skill');
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _fill(path: Path, with_meta: bool = True):
    c = sqlite3.connect(str(path))
    c.executescript(TWIN)
    for d in range(20):
        at = (T0 + timedelta(days=d)).isoformat()
        c.execute("INSERT INTO snapshots (endpoint, captured_at, payload, payload_hash, compressed, synthetic) VALUES (?, ?, ?, ?, 1, ?)",
                  ("tfr", at, zlib.compress(f'{{"day": {d}}}'.encode()), f"h{d}", 1 if d == 3 else 0))
    c.execute("INSERT INTO brief_archive (generated_at, brief_type, content) VALUES (?, 'ops', 'ops text')",
              ((T0 + timedelta(days=5)).isoformat(),))
    if with_meta:
        c.execute("INSERT INTO meta (key, value) VALUES ('window_end', ?)", ((T0 + timedelta(days=19)).isoformat(),))
    c.commit(); c.close()


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    monkeypatch.setattr(demo_api, "_window_end_cache", None)
    monkeypatch.setattr(demo_api, "_window_end_cache_checked", False)
    monkeypatch.setattr(demo_api, "_legacy_anchor_cache", None)
    monkeypatch.setattr(demo_api, "DEMO_ANCHOR_OVERRIDE", "")


@pytest.fixture
def sqlite_source(tmp_path, monkeypatch):
    path = tmp_path / "demo-source.db"; _fill(path)
    monkeypatch.setenv("DEMO_SOURCE", "sqlite")
    monkeypatch.setattr(demo_api, "DEMO_DB", str(path))
    return path


@pytest.fixture
def pg_source(tmp_path, monkeypatch):
    path = tmp_path / "demo-instance.db"; _fill(path)
    opened = []

    @contextmanager
    def fake_demo_pg_conn():
        c = sqlite3.connect(str(path)); c.row_factory = sqlite3.Row
        opened.append(c)
        try:
            yield c
        finally:
            c.close()

    monkeypatch.setenv("DEMO_SOURCE", "postgres")
    monkeypatch.setattr(demo_api.db_backend, "demo_pg_conn", fake_demo_pg_conn)
    monkeypatch.setattr(demo_api, "DEMO_DB", str(tmp_path / "does-not-exist.db"))
    return opened


def _check_logic():
    conn = demo_api._conn()  # noqa: SLF001
    try:
        assert demo_api._window_end(conn) == T0 + timedelta(days=19)  # noqa: SLF001
        assert demo_api._anchor(conn, 14) == T0 + timedelta(days=5)  # noqa: SLF001
        ready, span = demo_api._seed_ready(conn, 14)  # noqa: SLF001
        assert ready and span == 14
        vt = demo_api._virtual_timestamp(conn, 14, 1.0)  # noqa: SLF001
        assert T0 + timedelta(days=5) <= vt < T0 + timedelta(days=19)
        text, at, synthetic = demo_api._load_snapshot(conn, "tfr", T0 + timedelta(days=3, hours=1))  # noqa: SLF001
        assert text == '{"day": 3}' and at == (T0 + timedelta(days=3)).isoformat() and synthetic is True
        assert demo_api._load_snapshot(conn, "weather", vt) is None  # noqa: SLF001
    finally:
        conn.close()
    row = demo_api._brief_archive_lookup("ops", T0 + timedelta(days=6))  # noqa: SLF001
    assert row and row["content"] == "ops text" and row["brief_type"] == "ops"
    assert demo_api._brief_archive_lookup("weekly", T0 + timedelta(days=6)) is None  # noqa: SLF001
    assert [r["brief_type"] for r in demo_api._brief_archive_history(None, T0 + timedelta(days=6), 5)] == ["ops"]  # noqa: SLF001


def test_sqlite_source_logic_unchanged(sqlite_source):
    assert demo_api.source_kind() == "sqlite"
    _check_logic()


def test_postgres_source_same_logic_through_the_demo_pool(pg_source):
    assert demo_api.source_kind() == "postgres"
    _check_logic()
    assert pg_source, "the demo pool was used"


def test_legacy_anchor_fallback_by_name(tmp_path, monkeypatch):
    path = tmp_path / "old.db"; _fill(path, with_meta=False)
    monkeypatch.setenv("DEMO_SOURCE", "sqlite")
    monkeypatch.setattr(demo_api, "DEMO_DB", str(path))
    conn = demo_api._conn()  # noqa: SLF001
    try:
        assert demo_api._window_end(conn) is None  # noqa: SLF001
        assert demo_api._anchor(conn, 14) == T0  # noqa: SLF001
    finally:
        conn.close()


def test_unknown_source_is_an_error_not_a_fallback(monkeypatch):
    monkeypatch.setenv("DEMO_SOURCE", "mysql")
    with pytest.raises(RuntimeError, match="DEMO_SOURCE"):
        demo_api.source_kind()


def test_healthz_reports_the_source(sqlite_source, monkeypatch):
    from fastapi.testclient import TestClient
    client = TestClient(demo_api.app)
    body = client.get("/healthz").json()
    assert body["source"] == "sqlite" and body["ok"] is True and body["demo_db"] == str(sqlite_source)

    monkeypatch.setenv("DEMO_SOURCE", "postgres")

    @contextmanager
    def refusing():
        raise demo_api.db_backend.DemoIsolationError("synthetic refusal")
        yield  # noqa: unreachable -- keeps this a generator

    monkeypatch.setattr(demo_api.db_backend, "demo_pg_conn", refusing)
    body = client.get("/healthz").json()
    assert {k: body[k] for k in ("source", "loop_days", "ok", "error")} == {
        "source": "postgres", "loop_days": demo_api.LOOP_DAYS, "ok": False, "error": "DemoIsolationError"}
    assert "demo_db" not in body
    assert set(body) == {"source", "loop_days", "ok", "error", "started_at", "uptime_s", "requests", "profiles_store"}


def test_healthz_counts_api_requests_per_source(pg_source, tmp_path, monkeypatch):
    """2026-10-19 (relay-4 evidence): /api/ requests are counted under the
    source that answered them; /healthz itself is not counted; no path or
    client detail is kept, only the two integers."""
    from fastapi.testclient import TestClient
    monkeypatch.setattr(demo_api, "_REQUESTS", {"sqlite": 0, "postgres": 0})
    client = TestClient(demo_api.app)
    monkeypatch.setenv("DEMO_SOURCE", "postgres")
    client.get("/api/v1/demo/readiness"); client.get("/api/v1/demo/readiness")
    client.get("/healthz")
    body = client.get("/healthz").json()
    assert body["requests"] == {"sqlite": 0, "postgres": 2}
    assert body["source"] == "postgres" and body["ok"] is True
    assert body["started_at"].endswith("Z") and body["uptime_s"] >= 0
    sovereign = tmp_path / "sovereign.db"; _fill(sovereign)
    monkeypatch.setattr(demo_api, "DEMO_DB", str(sovereign))
    monkeypatch.setenv("DEMO_SOURCE", "sqlite")
    client.get("/api/v1/demo/readiness")
    assert client.get("/healthz").json()["requests"] == {"sqlite": 1, "postgres": 2}


def test_module_never_names_the_production_accessor():
    import ast
    src = Path(demo_api.__file__).read_text()
    names = {n.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Attribute)}
    assert "pg_conn" not in names and "demo_pg_conn" in names
