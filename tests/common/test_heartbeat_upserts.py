"""2026-10-19 (lean-out L2, F9): heartbeat upserts write on change only.

stdds_safety_status, itws_alerts and tbfm_sequences: an unchanged re-send
inside HEARTBEAT_TOUCH_S (300 s) writes nothing; once last_seen is older than
that it is touched; a content change is written at once. tfrs (no last_seen)
is left alone when unchanged. Isolated SQLite DB per test (never the live DB).
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import common.db as db  # noqa: E402

FMT = "%Y-%m-%dT%H:%M:%SZ"


@pytest.fixture
def hb_db(monkeypatch, tmp_path):
    p = tmp_path / "hb.db"
    monkeypatch.setattr(db, "_db_path", lambda: p)
    db.init_db_all()
    yield p


def _ago(s):
    return (datetime.utcnow() - timedelta(seconds=s)).strftime(FMT)


def _row(sql, args=()):
    with db.conn() as c:
        r = c.execute(sql, args).fetchone()
        return dict(r) if r else None


def _set(sql, args=()):
    with db.conn() as c:
        c.execute(sql, args)


# ── stdds_safety_status ─────────────────────────────────────────────────────

def test_stdds_unchanged_within_window_is_not_rewritten(hb_db):
    assert db.upsert_safety_status("KDCA", "C1", "0101", _ago(60)) is None
    stamp = _ago(60)
    _set("UPDATE stdds_safety_status SET last_seen=? WHERE airport='KDCA'", (stamp,))
    assert db.upsert_safety_status("KDCA", "C1", "0101", _ago(0)) == "0101"
    assert _row("SELECT last_seen FROM stdds_safety_status WHERE airport='KDCA'")["last_seen"] == stamp


def test_stdds_unchanged_after_window_touches_last_seen(hb_db):
    db.upsert_safety_status("KDCA", "C1", "0101", _ago(0))
    _set("UPDATE stdds_safety_status SET last_seen=? WHERE airport='KDCA'", (_ago(400),))
    now = _ago(0)
    assert db.upsert_safety_status("KDCA", "C1", "0101", now) == "0101"
    assert _row("SELECT last_seen FROM stdds_safety_status WHERE airport='KDCA'")["last_seen"] == now


def test_stdds_change_is_written_at_once_and_previous_returned(hb_db):
    db.upsert_safety_status("KDCA", "C1", "0101", _ago(0))
    assert db.upsert_safety_status("KDCA", "C1", "0111", _ago(0)) == "0101"
    assert _row("SELECT status_bitmask FROM stdds_safety_status WHERE airport='KDCA'")["status_bitmask"] == "0111"
    db.upsert_safety_status("KDCA", "C2", "0111", _ago(0))
    assert _row("SELECT control FROM stdds_safety_status WHERE airport='KDCA'")["control"] == "C2"


# ── itws_alerts ─────────────────────────────────────────────────────────────

def _itws(sev, detail, vt):
    db.upsert_itws_alert("KIAD", "microburst", sev, detail, vt, None,
                         json.dumps({"severity": sev, "detail": detail, "valid_time": vt}))


def test_itws_unchanged_within_window_is_not_rewritten(hb_db):
    _itws(4, "1 cell", _ago(60))
    stamp = _ago(60)
    _set("UPDATE itws_alerts SET last_seen=? WHERE airport='KIAD'", (stamp,))
    before = _row("SELECT * FROM itws_alerts WHERE airport='KIAD'")
    _itws(4, "1 cell", _ago(0))           # re-broadcast: new valid_time only
    after = _row("SELECT * FROM itws_alerts WHERE airport='KIAD'")
    assert after == before


def test_itws_unchanged_after_window_touches_heartbeat_fields(hb_db):
    _itws(4, "1 cell", _ago(400))
    _set("UPDATE itws_alerts SET last_seen=? WHERE airport='KIAD'", (_ago(400),))
    vt = _ago(0)
    _itws(4, "1 cell", vt)
    r = _row("SELECT * FROM itws_alerts WHERE airport='KIAD'")
    assert r["valid_time"] == vt and r["last_seen"] >= _ago(5)


def test_itws_change_is_written_at_once(hb_db):
    _itws(4, "1 cell", _ago(0))
    _itws(5, "2 cells", _ago(0))
    r = _row("SELECT * FROM itws_alerts WHERE airport='KIAD'")
    assert (r["severity"], r["detail"]) == (5, "2 cells")


# ── tbfm_sequences ──────────────────────────────────────────────────────────

def _tbfm(eta, seq=3):
    db.upsert_tbfm_sequence("SWANN", "ZDC", "TST123", eta, seq, 250, eta_kind="mfx")


def _tbfm_row():
    return _row("SELECT * FROM tbfm_sequences WHERE flight_id='TST123'")


def test_tbfm_unchanged_within_window_is_not_rewritten(hb_db):
    _tbfm("2026-10-19T18:00:00Z")
    stamp = _ago(60)
    _set("UPDATE tbfm_sequences SET last_seen=? WHERE flight_id='TST123'", (stamp,))
    _tbfm("2026-10-19T18:00:00Z")
    assert _tbfm_row()["last_seen"] == stamp


def test_tbfm_unchanged_after_window_touches_last_seen(hb_db):
    _tbfm("2026-10-19T18:00:00Z")
    _set("UPDATE tbfm_sequences SET last_seen=? WHERE flight_id='TST123'", (_ago(400),))
    _tbfm("2026-10-19T18:00:00Z")
    assert _tbfm_row()["last_seen"] >= _ago(5)


def test_tbfm_change_is_written_at_once(hb_db):
    _tbfm("2026-10-19T18:00:00Z")
    _set("UPDATE tbfm_sequences SET last_seen=? WHERE flight_id='TST123'", (_ago(60),))
    _tbfm("2026-10-19T18:02:00Z", seq=4)
    r = _tbfm_row()
    assert (r["eta"], r["sequence_num"]) == ("2026-10-19T18:02:00Z", 4)
    assert r["last_seen"] >= _ago(5)


def test_tbfm_queue_count_still_sees_an_untouched_row(hb_db):
    """A row left untouched for up to 300 s stays inside the 1800 s window."""
    _tbfm("2026-10-19T18:00:00Z")
    _set("UPDATE tbfm_sequences SET last_seen=? WHERE flight_id='TST123'", (_ago(299),))
    _tbfm("2026-10-19T18:00:00Z")
    assert db.get_active_tbfm_sequence_count("SWANN") == 1


# ── tfrs ────────────────────────────────────────────────────────────────────

def _count_updates(table):
    """Test-DB-only trigger counting row updates on `table`."""
    _set("CREATE TABLE IF NOT EXISTS _upd_count (t TEXT)")
    _set(f"CREATE TRIGGER IF NOT EXISTS _cnt_{table} AFTER UPDATE ON {table} "
         f"BEGIN INSERT INTO _upd_count VALUES ('{table}'); END")


def _updates(table):
    return _row("SELECT COUNT(*) AS n FROM _upd_count WHERE t=?", (table,))["n"]


def test_tfr_unchanged_is_not_rewritten_and_change_is_written(hb_db):
    _count_updates("tfrs")
    raw = json.dumps({"type": "SECURITY"})
    db.upsert_tfr("6/0001", raw, False, 1.0, 2e9)
    db.upsert_tfr("6/0001", raw, False, 1.0, 2e9)
    assert _updates("tfrs") == 0
    db.upsert_tfr("6/0001", raw, True, 1.0, 3e9, vip_push_reason="vip")
    assert _updates("tfrs") == 1
    r = _row("SELECT * FROM tfrs WHERE tfr_id='6/0001'")
    assert (r["is_vip"], r["effective_end"], r["vip_push_reason"]) == (1, 3e9, "vip")


def test_heartbeat_tables_write_nothing_on_an_unchanged_resend(hb_db):
    for t in ("stdds_safety_status", "itws_alerts", "tbfm_sequences"):
        _count_updates(t)
    db.upsert_safety_status("KDCA", "C1", "0101", _ago(0))
    _itws(4, "1 cell", _ago(0))
    _tbfm("2026-10-19T18:00:00Z")
    for _ in range(3):
        db.upsert_safety_status("KDCA", "C1", "0101", _ago(0))
        _itws(4, "1 cell", _ago(0))
        _tbfm("2026-10-19T18:00:00Z")
    assert [_updates(t) for t in ("stdds_safety_status", "itws_alerts", "tbfm_sequences")] == [0, 0, 0]
