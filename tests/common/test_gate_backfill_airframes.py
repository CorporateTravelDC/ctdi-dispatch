"""Phase D2 (2026-10-20): airframes.io history backfill. Mocked session only --
no network. Synthetic flights only; the token value is a test string."""
from __future__ import annotations

import calendar
import importlib.util
import time
from pathlib import Path

import pytest

_P = Path(__file__).resolve().parents[2] / "scripts" / "gate-backfill-airframes.py"
_spec = importlib.util.spec_from_file_location("gate_backfill_airframes", _P)
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)

NOW = float(calendar.timegm(time.strptime("2026-10-20T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ")))
OLD = "2026-10-18T06:00:00Z"            # 54 h old: eligible
RECENT = "2026-10-19T12:00:00Z"         # 24 h old: inside the validation window + bind margin
UA_ASSIGN = ("QUNDCULUA~1GATE ASSIGN UA0999/17 KORD KIAD SENT: 14:23:53Z ***ACCOMPLISH TERMINATION CHECKLIST*** "
             "GATE C9 AVAIL 1544 RAMP FREQ 129.075 OPS FREQ 130.500")


class R:
    def __init__(self, code, data=None):
        self.status_code, self._d = code, data

    def json(self):
        return self._d


class S:
    def __init__(self, pages):
        self.pages, self.calls, self.headers = list(pages), [], {}

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params))
        return self.pages.pop(0) if self.pages else R(404)


def msg(i, ts=OLD, text=UA_ASSIGN):
    return {"id": f"t{i}", "timestamp": ts, "text": text, "label": "RA", "direction": "up", "flight": ""}


def go(session, hours=1, **kw):
    got = []
    c = bf.run(hours=hours, dry_run=False, now=NOW, session=session, sleep=lambda s: None,
               ingest=lambda items, reason: (got.append((items, reason)) or {"received": len(items), "stored": 0}),
               **kw)
    return c, got


def test_dry_run_makes_no_network_call():
    c = bf.run(hours=72, dry_run=True, now=NOW)
    assert c["slices"] == 72 * len(bf.PHRASES) and c["fetched"] == 0 and c["min_seconds"] > 0


def test_plan_never_reaches_into_the_validation_window():
    last_until = max(u for _p, _s, u in bf.plan(72, NOW))
    assert last_until <= NOW - bf.BACKFILL_MIN_AGE_S - 3 * 3600


def test_429_stops_the_run():
    with pytest.raises(bf.Stopped):
        go(S([R(429)]))


def test_401_stops_the_run():
    with pytest.raises(bf.Stopped):
        go(S([R(401)]))


def test_offset_paging_until_a_short_page():
    full = [msg(i) for i in range(bf.PAGE)]
    s = S([R(200, full), R(200, [msg(999)])])
    c, _ = go(s)
    assert [p["offset"] for p in s.calls[:2]] == [0, bf.PAGE]
    assert c["fetched"] >= bf.PAGE + 1


def test_duplicates_across_phrases_and_recent_messages_are_dropped():
    s = S([R(200, [msg(1), msg(1), msg(2, ts=RECENT)])])
    c, got = go(s)
    assert c["unique"] == 2 and c["too_recent"] == 1 and c["candidates"] == 1
    items, reason = got[0]
    assert reason == "backfill:airframes"
    assert "text" not in items[0] and items[0]["hit"]["gate"] == "C9" and items[0]["source"] == "AIRFRAMES"


def test_token_is_never_logged(monkeypatch):
    secret = "test-token-not-real-7f3a"
    monkeypatch.setenv("AIRFRAMES_TOKEN", secret)
    lines = []

    class Fake(S):
        pass

    import types
    fake_requests = types.SimpleNamespace(Session=lambda: Fake([R(200, [msg(1)])]))
    monkeypatch.setitem(__import__("sys").modules, "requests", fake_requests)
    bf.run(hours=1, dry_run=False, now=NOW, sleep=lambda s: None, log=lines.append,
           ingest=lambda items, reason: {"received": len(items)})
    assert lines and not any(secret in ln for ln in lines)


def test_backfill_item_binds_and_stores_through_the_live_ingest(monkeypatch):
    import common.db as db
    from common import flight_instance as fi
    from common import gate_assignments as ga
    db.init_db_all()
    fi.clear_alias_cache()
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda ids: set())
    monkeypatch.setattr(db, "faa_ladd_hex_batch", lambda hx: set())
    with db.conn() as c:
        c.execute("INSERT INTO flight_ooooi_times (gufi, callsign, airline, flight_num, origin, destination, "
                  "original_departure, original_arrival, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  ("G-UA999", "UAL999", "UAL", "999", "KORD", "KIAD", "2026-10-17T12:30:00Z",
                   "2026-10-17T15:10:00Z", NOW))
    m = msg(1, ts="2026-10-17T14:23:55Z")
    counts = bf.run(hours=80, dry_run=False, now=NOW, session=S([R(200, [m])]), sleep=lambda s: None,
                    log=lambda *_: None)
    assert counts["stored"] == 1 and counts.get("ladd_dropped", 0) == 0
    with db.conn() as conn:
        reasons = {r["reason"] for r in conn.execute("SELECT reason FROM gate_baggage_assignment_history")}
    assert reasons == {"backfill:airframes"}
    fi.clear_alias_cache()
