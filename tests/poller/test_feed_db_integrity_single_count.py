"""feed-db-integrity-check: one statement per critical field (2026-10-18,
lean-out L1). _critical_field_null_rate() ran two COUNTs over the same hour
of flight_events (34 runs/24 h over 500 ms, 10.3 s avg); it now runs one
`COUNT(*), COUNT(*) FILTER (...)`. Same totals, same None-on-empty, same
findings."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from common import db  # noqa: E402
from poller.skills import feed_db_integrity_check as fic  # noqa: E402


class _CountingConn:
    def __init__(self, c):
        self.c = c
        self.statements = []

    def execute(self, sql, *a):
        self.statements.append(sql)
        return self.c.execute(sql, *a)


def _flight(fid, updated_at, arrival_time):
    with db.conn() as c:
        c.execute("INSERT INTO flight_events (flight_id, updated_at, arrival_time) VALUES (?, ?, ?)",
                  (fid, updated_at, arrival_time))


def _rate():
    with db.conn() as c:
        cc = _CountingConn(c)
        out = fic._critical_field_null_rate(cc, "flight_events", "updated_at", "epoch", "arrival_time")
    return out, cc.statements


def test_one_statement_same_counts():
    db.init_db_all()
    now = time.time()
    _flight("a", now - 60, None)
    _flight("b", now - 120, 0)
    _flight("c", now - 180, now + 600)
    _flight("old", now - 7200, None)          # outside the hour
    out, stmts = _rate()
    assert out == (3, 2)
    assert len(stmts) == 1 and "FILTER" in stmts[0]


def test_no_recent_rows_is_none():
    db.init_db_all()
    _flight("old", time.time() - 7200, None)
    out, stmts = _rate()
    assert out is None and len(stmts) == 1


def test_all_null_finding_unchanged(monkeypatch):
    db.init_db_all()
    now = time.time()
    _flight("a", now - 60, None)
    _flight("b", now - 90, None)
    monkeypatch.setattr(db, "get_feed_states",
                        lambda: [{"feed_name": "push:fdps", "fetched_at": now - 30, "error": None}])
    monkeypatch.setattr(fic, "FEED_TABLE_MAP", {"push:fdps": fic.FEED_TABLE_MAP["push:fdps"]})
    findings = fic.run_check()
    assert findings == ["push:fdps: flight_events.arrival_time is null on ALL 2 rows written "
                        "in the last hour -- field-level gap, table itself is receiving writes."]
