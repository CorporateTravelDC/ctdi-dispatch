"""
tests/poller/test_tbfm_arrival_enrichment_pass.py

2026-10-04: corporatetraveldc-tbfm-arrival-enrichment logged
"enrichment pass failed: 'NoneType' object has no attribute 'rowcount'" on
every 5-minute run since the Postgres cutover (200+ journal hits, exit 0, no
failed unit). Root cause: db_backend's executemany() shims returned the
driver's return value -- None under psycopg3 -- where sqlite3 returns the
cursor, and db.enrich_flight_arrival_times() reads .rowcount off it.

These tests pin (1) the executemany shim contract on both the connection and
cursor wrappers (return something with .rowcount), (2) a full run_enrichment()
pass end to end under the sqlite guard with a seeded flight_events row and an
active tbfm_sequences row, and (3) that main() now exits non-zero when the
pass fails instead of swallowing it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import common.db as db
from common import db_backend
from ingest.parsers.fdps_parser import write_flight_event
from poller.skills import tbfm_arrival_enrichment as skill
from poller.skills.faa_cifp_parse import _parse_lines

FIXTURE = Path(__file__).parent / "fixtures" / "cifp_kiad_gibbz6_sample.txt"


@pytest.fixture
def enrich_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db, "_db_path", lambda: db_path)
    db.init_db_all()
    fixes, legs, holds = _parse_lines(FIXTURE.read_text().splitlines(), "260903")
    db.cifp_replace_all("260903", fixes, legs, holds)
    yield db_path


class _FakePgCursor:
    """psycopg3 shape: executemany() returns None, rowcount lives on the cursor."""
    rowcount = 3

    def executemany(self, sql, params_seq, **kw):
        self.kw = kw
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakePgConn:
    def __init__(self):
        self._cur = _FakePgCursor()

    def cursor(self, *a, **kw):
        return self._cur


def test_translating_connection_executemany_returns_cursor_with_rowcount():
    c = db_backend._TranslatingConnection(_FakePgConn())
    cur = c.executemany("UPDATE t SET a = ? WHERE b = ?", [(1, "x"), (2, "y")])
    assert cur is not None and cur.rowcount == 3
    # psycopg only accumulates rowcount across the batch with returning=True
    assert cur.kw.get("returning") is True


def test_translating_cursor_executemany_returns_self():
    cur = db_backend._TranslatingCursor(_FakePgCursor())
    out = cur.executemany("UPDATE t SET a = ? WHERE b = ?", [(1, "x")])
    assert out.rowcount == 3


def test_enrich_flight_arrival_times_returns_count_on_sqlite(enrich_db):
    assert write_flight_event({
        "callsign": "UAL123", "gufi": "GUFI-ENRICH-1", "origin": "KORD",
        "destination": "KIAD", "aircraft_type": "B738", "latitude": 39.0,
        "longitude": -77.0, "altitude_ft": 12000, "ground_speed": 280,
        "raw_xml": "<test/>", "flight_status": "enroute",
    })
    n = db.enrich_flight_arrival_times([(1_800_000_000.0, "GUFI-ENRICH-1"),
                                        (1_800_000_000.0, "GUFI-DOES-NOT-EXIST")])
    assert n == 1


def test_run_enrichment_end_to_end_updates_arrival_time(enrich_db):
    assert write_flight_event({
        "callsign": "UAL123", "gufi": "GUFI-ENRICH-2", "origin": "KORD",
        "destination": "KIAD", "aircraft_type": "B738", "latitude": 39.0,
        "longitude": -77.0, "altitude_ft": 12000, "ground_speed": 280,
        "raw_xml": "<test/>", "flight_status": "enroute",
    })
    eta = (datetime.now(timezone.utc) + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.upsert_tbfm_sequence("SWANN", "ZDC", "UAL123", eta, 3, 250, eta_kind="mfx")
    with db.conn() as c:
        before = c.execute(
            "SELECT arrival_time FROM flight_events WHERE flight_id=?", ("GUFI-ENRICH-2",)).fetchone()
    result = skill.run_enrichment()
    assert result["candidates"] >= 1
    assert result["updated"] == 1
    with db.conn() as c:
        after = c.execute(
            "SELECT arrival_time FROM flight_events WHERE flight_id=?", ("GUFI-ENRICH-2",)).fetchone()
    assert after["arrival_time"] is not None
    assert after["arrival_time"] != before["arrival_time"]


def test_main_exits_nonzero_when_pass_fails(enrich_db):
    with patch.object(skill, "run_enrichment", side_effect=AttributeError(
            "'NoneType' object has no attribute 'rowcount'")):
        assert skill.main() == 1


def test_main_exits_zero_on_success(enrich_db):
    with patch.object(skill, "run_enrichment", return_value={"candidates": 0, "updated": 0}):
        assert skill.main() == 0
