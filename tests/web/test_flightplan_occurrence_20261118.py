"""
tests/web/test_flightplan_occurrence_20261118.py -- 2026-11-18 (UA4006 KSAV-KIAD): with no
plan yet for today's leg, /api/v1/flightplan/{callsign} answered with yesterday's row as
"confirmed: true, status: active". Every answer now carries occurrence_date (America/New_York
departure date) and current_occurrence; only the current occurrence is confirmed, and an
older row reads status "stale". Synthetic identifiers; db calls monkeypatched; LADD unlisted.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import web.main as web_main  # noqa: E402
from common import db  # noqa: E402

NY = ZoneInfo("America/New_York")


def _get(monkeypatch, plan):
    monkeypatch.setattr(db, "get_flight_plan_by_callsign", lambda cs: dict(plan))
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda batch: set())
    monkeypatch.setattr(db, "faa_ladd_hex_batch", lambda batch: set())
    r = TestClient(web_main.app).get("/api/v1/flightplan/zzz4006")
    assert r.status_code == 200
    return r.json()


def test_yesterdays_landed_row_is_stale_not_confirmed(monkeypatch):
    now = time.time()
    out = _get(monkeypatch, {"origin": "KSAV", "destination": "KIAD", "status": "active",
                             "departure_time": None, "arrival_time": now - 23 * 3600,
                             "updated_at": now - 23 * 3600 - 600})
    assert out["confirmed"] is False and out["current_occurrence"] is False
    assert out["status"] == "stale"
    assert out["occurrence_date"] == datetime.fromtimestamp(now - 23 * 3600, NY).date().isoformat()
    assert "earlier occurrence" in out["reason"]


def test_todays_row_is_confirmed_with_its_date(monkeypatch):
    now = time.time()
    out = _get(monkeypatch, {"origin": "KSAV", "destination": "KIAD", "status": "active",
                             "departure_time": now - 300, "arrival_time": now + 3600, "updated_at": now})
    assert out["confirmed"] is True and out["current_occurrence"] is True and out["status"] == "active"
    assert out["occurrence_date"] == datetime.fromtimestamp(now - 300, NY).date().isoformat()
    assert "reason" not in out
