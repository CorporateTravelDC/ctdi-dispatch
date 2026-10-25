"""
tests/web/test_flightplan_ladd_20261025.py -- Tier-0 /api/v1/flightplan/{callsign}
had no LADD check (2026-10-25 adversarial sweep). A listed callsign, a plan
whose registration or hex is listed, and a failed LADD lookup (fail closed)
must all answer exactly like "no plan on file". Synthetic identifiers only;
db calls are monkeypatched.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import web.main as web_main  # noqa: E402
from common import db  # noqa: E402

PLAN = {"origin": "KDCA", "destination": "KBOS", "aircraft_type": "C56X", "status": "active",
        "departure_time": 1.0, "arrival_time": 2.0, "registration": "N0TEST", "hex": "a00000"}


def _get(monkeypatch, listed_ids=(), listed_hex=(), fail=False):
    monkeypatch.setattr(db, "get_flight_plan_by_callsign", lambda cs: dict(PLAN))

    def ids(batch):
        if fail:
            raise RuntimeError("db down")
        return {i for i in batch if i in listed_ids}

    monkeypatch.setattr(db, "faa_ladd_listed_batch", ids)
    monkeypatch.setattr(db, "faa_ladd_hex_batch", lambda batch: {h for h in batch if h in listed_hex})
    r = TestClient(web_main.app).get("/api/v1/flightplan/tst123")
    assert r.status_code == 200
    return r.json()


def test_unlisted_plan_is_returned(monkeypatch):
    out = _get(monkeypatch)
    assert out["confirmed"] is True and out["origin"] == "KDCA"


def test_listed_callsign_reads_as_a_miss(monkeypatch):
    miss = _get(monkeypatch, listed_ids={"TST123"})
    assert miss["confirmed"] is False and "origin" not in miss
    assert miss["reason"] == "No FDPS flight plan on file for this callsign"


def test_listed_registration_or_hex_reads_as_a_miss(monkeypatch):
    assert _get(monkeypatch, listed_ids={"N0TEST"})["confirmed"] is False
    assert _get(monkeypatch, listed_hex={"a00000"})["confirmed"] is False


def test_failed_ladd_lookup_fails_closed(monkeypatch):
    assert _get(monkeypatch, fail=True)["confirmed"] is False
