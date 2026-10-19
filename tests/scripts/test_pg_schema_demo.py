"""tests/scripts/test_pg_schema_demo.py -- 2026-10-16 demo/production database
split: src/common/pg_schema_demo/ creates the six demo tables (plus the
runner's own schema_migrations) and NOTHING of production's operational
schema, and scripts/pg_migrate.py --instance demo reads the demo dir and the
DEMO_PG_* identity (no connection is made).

Three names coincide with production's by design -- demo_profiles,
chat_messages, brief_archive -- because the code that reads them
(src/demo/profiles.py, src/runner/main.py, src/demo/demo_api.py) is unchanged
by the split and pointed at the demo instance by DISPATCH_ROLE / DEMO_SOURCE
alone. Every other PG_TABLES name, and anything LADD-, auth-, token- or
audit-shaped, is refused here.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from common import db_backend  # noqa: E402

# captured at import, before the conftest tripwire replaces it per test
REAL_DEMO_CONNINFO = db_backend._demo_pg_conninfo  # noqa: SLF001

DEMO_DIR = REPO / "src/common/pg_schema_demo"
DEMO_TABLES = {"snapshots", "brief_archive", "meta", "demo_profiles", "chat_messages", "scrub_ledger"}
SHARED_SHAPE = {"demo_profiles", "chat_messages", "brief_archive"}
CREATE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)", re.I)
FORBIDDEN_SHAPES = re.compile(r"ladd|auth|token|audit|signer|approval|watchlist|flight|registry|oauth|session", re.I)


def created_tables() -> set[str]:
    out = set()
    for f in sorted(DEMO_DIR.glob("*.sql")):
        out |= set(CREATE_RE.findall(f.read_text()))
    return out


def test_demo_schema_creates_exactly_the_six_plus_bookkeeping():
    assert created_tables() == DEMO_TABLES | {"schema_migrations"}


def test_demo_schema_never_creates_a_production_table():
    tables = created_tables() - {"schema_migrations"}
    assert not (tables & (db_backend.PG_TABLES - SHARED_SHAPE)), tables & db_backend.PG_TABLES
    assert not [t for t in tables if FORBIDDEN_SHAPES.search(t)]
    text = "\n".join(f.read_text() for f in DEMO_DIR.glob("*.sql"))
    for bad in ("faa_ladd", "auth_tokens", "audit_log", "flight_events", "watchlist_entries", "osint", "board_"):
        assert bad not in text, bad


def test_demo_schema_names_no_password_and_locks_the_app_role():
    for f in DEMO_DIR.glob("*.sql"):
        assert re.search(r"PASSWORD\s+'", f.read_text(), re.I) is None, f"a role password literal in {f.name}"
    text = (DEMO_DIR / "0001_demo_core.sql").read_text()
    assert "ENCRYPTED PASSWORD" not in text.upper()
    assert "CREATE ROLE demo LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE" in text
    assert "GRANT SELECT, INSERT, UPDATE ON demo_profiles TO demo" in text
    assert "GRANT SELECT, INSERT, DELETE ON chat_messages TO demo" in text
    assert re.search(r"GRANT SELECT ON snapshots, brief_archive, meta, scrub_ledger, schema_migrations TO demo", text)
    assert "DELETE ON snapshots" not in text and "UPDATE ON snapshots" not in text


def test_demo_snapshot_shape_matches_the_sovereign_file():
    """The columns scripts/scrub-demo-source.py and src/demo/demo_api.py use,
    including `synthetic`, and the idempotency key for re-run backfills."""
    text = (DEMO_DIR / "0001_demo_core.sql").read_text()
    for col in ("endpoint", "captured_at", "payload", "payload_hash", "compressed", "synthetic"):
        assert re.search(rf"^\s+{col}\s", text, re.M), col
    assert "UNIQUE (endpoint, captured_at, synthetic)" in text
    assert "prev_hash          TEXT    NOT NULL UNIQUE" in text and "entry_hash         TEXT    NOT NULL UNIQUE" in text


# 2026-10-19 grants re-audit (relay 2): the exact privilege set the `demo` role
# holds after 0002 -- the same table scripts/demo-isolation-check.sh section 4
# compares the live instance against.
EXPECTED_DEMO_GRANTS = {
    "snapshots": {"SELECT"}, "brief_archive": {"SELECT"}, "meta": {"SELECT"}, "scrub_ledger": {"SELECT"},
    "demo_profiles": {"SELECT", "INSERT", "UPDATE"},
    "chat_messages": {"SELECT", "INSERT", "DELETE"},
}
GRANT_RE = re.compile(r"^GRANT\s+([A-Z, ]+?)\s+ON\s+(?!SCHEMA|DATABASE|ALL)([a-z_, ]+?)\s+TO\s+demo;", re.M)


def test_grants_reaudit_0002_is_exactly_the_expected_set():
    text = (DEMO_DIR / "0002_demo_grants.sql").read_text()
    lines = [l for l in text.splitlines() if l and not l.startswith("--")]
    body = "\n".join(lines)
    # everything is revoked first, then re-granted
    assert body.index("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM demo;") < body.index("GRANT SELECT ON")
    assert "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM demo;" in body
    assert "REVOKE ALL ON DATABASE corporatetraveldc_demo FROM PUBLIC;" in body
    assert "REVOKE ALL ON SCHEMA public FROM PUBLIC;" in body
    granted: dict[str, set] = {}
    for privs, tables in GRANT_RE.findall(body):
        for t in (x.strip() for x in tables.split(",")):
            granted.setdefault(t, set()).update(x.strip() for x in privs.split(","))
    assert granted == EXPECTED_DEMO_GRANTS
    # the four playback tables: SELECT only, never a write
    for t in ("snapshots", "brief_archive", "meta", "scrub_ledger"):
        assert granted[t] == {"SELECT"}, t
    assert set(granted) == DEMO_TABLES, "grants name exactly the six demo tables"
    for bad in ("TRUNCATE", "REFERENCES", "TRIGGER", "ALL PRIVILEGES", "GRANT ALL", "CREATE ON SCHEMA",
                "SEQUENCES IN SCHEMA public TO demo", "schema_migrations TO demo", "WITH GRANT OPTION"):
        assert bad not in body, bad
    assert re.search(r"ALTER ROLE demo NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS "
                     r"CONNECTION LIMIT \d+;", body)
    assert re.search(r"PASSWORD", body, re.I) is None, "no password in tracked DDL"


def test_live_check_and_schema_agree_on_the_grant_set():
    """scripts/demo-isolation-check.sh carries the same set, one
    table:PRIVILEGE token per grant, sorted."""
    sh = (REPO / "scripts/demo-isolation-check.sh").read_text()
    want = " ".join(sorted(f"{t}:{p}" for t, ps in EXPECTED_DEMO_GRANTS.items() for p in ps))
    assert f'DEMO_ROLE_GRANTS="{want}"' in sh


def _load_migrate():
    spec = importlib.util.spec_from_file_location("pg_migrate", REPO / "scripts/pg_migrate.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_pg_migrate_instance_demo_uses_the_demo_dir_and_identity(monkeypatch, tmp_path):
    m = _load_migrate()
    assert [p.name for p in m.migration_files("demo")] == ["0001_demo_core.sql", "0002_demo_grants.sql"]
    assert m.schema_dir("production") == REPO / "src/common/pg_schema"
    assert "0001_demo_core.sql" not in [p.name for p in m.migration_files("production")]

    monkeypatch.setattr(db_backend, "_demo_pg_conninfo", REAL_DEMO_CONNINFO)
    monkeypatch.setattr(db_backend, "backend", lambda: "postgres")
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "postgres")
    for v in ("DEMO_PG_USER", "DEMO_PG_PASSWORD", "DEMO_PG_HOST", "DEMO_PG_DB"):
        monkeypatch.delenv(v, raising=False)
    # a non-owner role is refused before any conninfo is built
    monkeypatch.setenv("DEMO_PG_USER", "demo")
    assert "demo_owner" in (m.prepare_demo_env() or "")
    monkeypatch.delenv("DEMO_PG_USER")
    secrets = tmp_path / "pgsql-demo-secrets.env"
    secrets.write_text("# header\nPOSTGRES_PASSWORD=synthetic-owner-sentinel\n")
    monkeypatch.setattr(m, "DEMO_SECRETS_FILE", secrets)
    assert m.prepare_demo_env() is None
    assert os.environ["DEMO_PG_USER"] == "demo_owner"
    ci = m.build_conninfo(None, "demo")
    assert "dbname=corporatetraveldc_demo" in ci and "user=demo_owner" in ci and "password=synthetic-owner-sentinel" in ci
    assert "dbname=corporatetraveldc " not in ci + " "
    # the production instance never sees the demo identity, and vice versa
    monkeypatch.setenv("DEMO_PG_DB", db_backend.PRODUCTION_PG_DB)
    with pytest.raises(db_backend.DemoIsolationError):
        m.build_conninfo(None, "demo")
