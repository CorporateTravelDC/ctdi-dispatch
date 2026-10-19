"""2026-10-18 (gate batch): common.gate_assignments / gate_aeroapi on REAL Postgres,
against pg_schema/0079 -- only ever in a throwaway schema of the scratch database
corporatetraveldc_pgphase1_test (tests/conftest.py's postgres_scratch guard refuses
any other database). Skips when Postgres or the scratch database is unreachable.
Covers what SQLite cannot: BOOLEAN / identity columns, ON CONFLICT on the real
constraints, rowcount through the translating connection, the AeroAPI ledger's
UNIQUE slot. Synthetic flights only."""
from __future__ import annotations

import contextlib
import os
import uuid
from pathlib import Path
from unittest import mock

import pytest

from common import config, db_backend

REPO = Path(__file__).resolve().parents[2]
SCRATCH_DB = "corporatetraveldc_pgphase1_test"
PG_ENV = {"DISPATCH_DB_BACKEND": "postgres"}


def _conninfo() -> str:
    with mock.patch.dict(os.environ, PG_ENV):
        return db_backend._pg_conninfo().replace(  # noqa: SLF001
            f"dbname={config.get('DISPATCH_PG_DB', 'corporatetraveldc')}", f"dbname={SCRATCH_DB}")


def _reachable() -> bool:
    try:
        import psycopg
        with psycopg.connect(_conninfo(), connect_timeout=3) as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = [pytest.mark.postgres_scratch,
              pytest.mark.skipif(not _reachable(), reason=f"scratch database {SCRATCH_DB!r} unreachable")]


@pytest.fixture
def pg(monkeypatch):
    import psycopg
    from psycopg.rows import dict_row
    import common.db as db
    ci = _conninfo()
    assert f"dbname={SCRATCH_DB}" in ci
    schema = f"gate_test_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(ci, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    raw = psycopg.connect(ci, row_factory=dict_row, autocommit=False)
    raw.execute(f'SET search_path TO "{schema}"')
    raw.execute((REPO / "src/common/pg_schema/0079_gate_baggage_assignments.sql").read_text())
    raw.commit()

    @contextlib.contextmanager
    def _conn():
        try:
            yield db_backend._TranslatingConnection(raw)  # noqa: SLF001
            raw.commit()
        except Exception:
            raw.rollback()
            raise

    monkeypatch.setattr(db, "conn", _conn)
    monkeypatch.setattr(db_backend, "backend", lambda: "postgres")
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda ids: set())
    monkeypatch.setattr(db, "faa_ladd_hex_batch", lambda hx: set())
    try:
        yield raw
    finally:
        raw.close()
        admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.close()


def test_record_resolve_replay_and_history_on_postgres(pg):
    from common import gate_assignments as ga
    key = ga.make_key("UAL", "981", "DCA", "ORD", "2026-10-17")
    now = 1792245600.0
    mk = lambda src, v, t, ev="clearance", k=None: ga.Observation(  # noqa: E731
        key=key, side="departure", field="gate", value_raw=v, source=src, observed_at=t,
        leg_match="exact" if src != "mwaa_fids" else "route_watch", evidence_type=ev,
        confidence=0.95 if src != "mwaa_fids" else 0.7, parser_version="t", airport="DCA", event_key=k)
    r = ga.record([mk("mwaa_fids", "C1", "2026-10-17T12:00:00Z", "display")], now=now)
    assert r.stored == 1 and len(r.changes) == 1
    r = ga.record([mk("mwaa_fids", "C1", "2026-10-17T12:00:00Z", "display")], now=now + 60)
    assert r.stored == 0 and r.duplicates == 1 and r.changes == []         # last_seen_at moved only
    r = ga.record([mk("swim_tdes", "G", "2026-10-17T12:01:00Z", k="a")], now=now + 60)
    assert r.changes == []                                                 # placeholder: nothing
    r = ga.record([mk("swim_tdes", "C27", "2026-10-17T12:02:00Z", k="b")], now=now + 60)
    assert r.changes[0].resolution.value == "C27" and r.changes[0].revision == 2
    v = ga.leg_assignments(key, now + 60)["departure_gate"]
    assert (v["value"], v["status"], v["source"]) == ("C27", "observed", "SWIM TDES")
    assert v["superseded"][0]["value"] == "C1"
    rows = pg.execute("SELECT is_placeholder, value_norm FROM gate_baggage_observations ORDER BY id").fetchall()
    assert [r["is_placeholder"] for r in rows] == [False, True, False]
    assert ga.legs_for("UA981", sched_date="2026-10-17", now=now)[0]["leg_key"] == str(key)


def test_aeroapi_ledger_slot_is_unique_on_postgres(pg):
    from common import gate_aeroapi as gaa
    now = 1792245600.0
    assert gaa._claim("UAL981 ORD-BOS 2026-10-17", "pre_eta", now) is not None
    assert gaa._claim("UAL981 ORD-BOS 2026-10-17", "pre_eta", now + 5) is None
    assert gaa.calls_today(now) == 1
    assert gaa.calls_for_leg("UAL981 ORD-BOS 2026-10-17") == {"pre_eta"}
