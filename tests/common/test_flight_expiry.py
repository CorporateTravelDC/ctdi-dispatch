"""flight_events expiry sweep (common/flight_expiry.py, 2026-11-19): rows that will never get another
message are closed as 'expired', in bounded batches, and /api/v1/flightplan never serves them."""
from __future__ import annotations

from common import db, flight_expiry as fx

NOW = 1_795_060_000.0
H = 3600.0


def _row(fid, flight_num, updated_at, *, arrival=None, lat=None, status="active", dest="KIAD"):
    db.upsert_flight_event(fid, "UAL", flight_num, "KEWR", dest, None, None, arrival, status,
                           lat, None if lat is None else -77.0, None, None, "<flight/>")
    with db.conn() as c:
        c.execute("UPDATE flight_events SET updated_at = ?, status = ? WHERE flight_id = ?",
                  (updated_at, status, fid))


def _status(fid):
    with db.conn() as c:
        return c.execute("SELECT status FROM flight_events WHERE flight_id = ?", (fid,)).fetchone()[0]


def test_rules_and_boundaries():
    db.init_db_all() if hasattr(db, "init_db_all") else db.init_db()
    _row("a1", "1", NOW - 3.5 * H, arrival=NOW - 3.5 * H)          # arrived >3 h ago, quiet >3 h -> expired
    _row("a2", "2", NOW - 3.5 * H, arrival=NOW - 1 * H)            # ETA only 1 h ago -> stays
    _row("p1", "3", NOW - 6.5 * H, lat=38.9)                       # position, quiet >6 h -> expired
    _row("p2", "4", NOW - 5 * H, lat=38.9)                         # position, quiet 5 h -> stays
    _row("q1", "5", NOW - 25 * H)                                  # nothing, quiet >24 h -> expired
    _row("q2", "6", NOW - 20 * H)                                  # nothing, quiet 20 h -> stays
    _row("old", "7", NOW - 300 * H)                                # backlog -> expired
    _row("cx", "8", NOW - 300 * H, status="cancelled")             # never touched
    _row("fresh", "9", NOW - 0.5 * H, arrival=NOW - 10 * H)        # still updating -> stays
    out = fx.sweep(now=NOW)
    assert {f: _status(f) for f in ("a1", "a2", "p1", "p2", "q1", "q2", "old", "cx", "fresh")} == {
        "a1": "expired", "a2": "active", "p1": "expired", "p2": "active", "q1": "expired",
        "q2": "active", "old": "expired", "cx": "cancelled", "fresh": "active"}
    assert out["recent"] == 3 and out["backlog"] == 1


def test_backlog_is_batched_with_a_cursor():
    db.init_db_all() if hasattr(db, "init_db_all") else db.init_db()
    for i in range(5):
        _row(f"b{i}", f"9{i}", NOW - (400 - i) * H)
    first = fx.sweep(now=NOW, batch=2)
    assert first["backlog"] == 2
    second = fx.sweep(now=NOW, batch=2)
    assert second["backlog"] == 2 and second["cursor"] >= first["cursor"]
    fx.sweep(now=NOW, batch=2)
    assert all(_status(f"b{i}") == "expired" for i in range(5))


def test_flightplan_lookup_never_serves_expired():
    db.init_db_all() if hasattr(db, "init_db_all") else db.init_db()
    _row("e1", "9999", NOW - 30 * H)
    fx.sweep(now=NOW)
    assert _status("e1") == "expired"
    assert db.get_flight_plan_by_callsign("UAL9999") is None
    assert db.get_flight_plan_by_callsign("UAL9999", destination_hint="KIAD") is None
