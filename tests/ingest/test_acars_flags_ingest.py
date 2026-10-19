"""2026-10-18: the ingest ACARS writer stamps acars_messages.acars_flags, puts the flags
in the watchlist event detail, and ties them to TFRs through the stored areas_geo.
OOOI tie: a flag is a label -- classification can never assert a phase; phases stay on
the gated path (db.update_watchlist_oooi_phase_authoritative, SWIM asserts, ADS-B never
asserts ON/IN). Synthetic data only (N0... registrations cannot be real)."""
from __future__ import annotations

import json
import time

import pytest

import common.db as db
import ingest.local_airspace as la

VDL2_OFF = {"vdl2": {"freq": 136975000, "station": "SYNTH-VDL",
                     "avlc": {"src": {"addr": "A0B0C0", "type": "Aircraft"},
                              "acars": {"reg": ".N0TEST1", "flight": "TST101", "label": "QN",
                                        "blk_id": "2", "ack": "!", "mode": "2",
                                        "msg_text": "OFF 1912"}}}}
POS_IN_TFR = {"tail": "N0TEST1", "flight": "TST101", "label": "H1", "block_id": "7",
              "text": "#M1BPOSN38512W077123,FIX01,191200,350"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    db.init_db_all()
    la._tfr_areas_cache.update(at=-1e18, areas=[])
    entry = {"id": "wl-synth-1", "entry_type": "flight", "identifier": "TST101", "registration": "N0TEST1",
             "tier": "temporary"}
    import shared.vip_listed as vl
    monkeypatch.setattr(vl, "PERMANENT_WATCHLIST_DIR", tmp_path / "no-watchlists")   # no file mounted
    vl._cache.update(mtime=None, by_id={})
    monkeypatch.setattr(la, "get_active_entries", lambda entry_type=None, cached=False: [entry])
    class _Hits(list):
        pass
    hits = _Hits()
    monkeypatch.setattr(la, "watchlist_event_hit", lambda eid, summary, detail, priority=3:
                        hits.append((eid, detail, priority, summary)))
    hits.entry = entry

    def _no_phase(*a, **k):
        raise AssertionError("ACARS classification must never write an OOOI phase")
    monkeypatch.setattr(db, "update_watchlist_oooi_phase", _no_phase)
    monkeypatch.setattr(db, "update_watchlist_oooi_phase_authoritative", _no_phase)
    return hits


def _rows():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT msg_type, acars_flags, watchlist_hit FROM acars_messages")]


def _tfr(areas, start=None):
    now = time.time()
    db.upsert_tfr("6/9001", json.dumps({"type": "SECURITY", "areas_geo": areas}), False,
                  start if start is not None else now - 60, now + 3600, vip_push_reason="security_legal_basis")


def test_oooi_flag_is_event_detail_never_a_phase(env):
    la._process_acars_message(VDL2_OFF)
    assert _rows() == [{"msg_type": "OFF", "acars_flags": "oooi", "watchlist_hit": 1}]
    assert len(env) == 1
    eid, detail, priority, _summary = env[0]
    assert detail["acars_flags"] == ["oooi"] and detail["watchlist_trigger"] == "acars_off"
    assert priority == 4
    with db.conn() as c:                                     # nothing touched the phase columns
        assert c.execute("SELECT COUNT(*) FROM watchlist_entries WHERE oooi_phase IS NOT NULL").fetchone()[0] == 0


def test_flags_cannot_pass_the_authority_gate():
    """Even if a caller fed a flagged local message to the gated writer, the gate
    decides by source: a local ACARS receiver may not assert OFF/ON/IN while SWIM is
    live, whatever the message's flags say."""
    ok, _ = db._oooi_authority_check("on", "local_acars", {})
    assert ok is False


def test_watched_position_inside_active_tfr_flags_tfr_area(env):
    _tfr([{"name": "A", "lat": 38.85, "lon": -77.2, "radius_nm": 3.0}])
    la._process_acars_message(POS_IN_TFR)
    assert _rows()[-1]["acars_flags"] == "position_report,tfr_area"


def test_unwatched_position_gets_no_tfr_area(env, monkeypatch):
    _tfr([{"name": "A", "lat": 38.85, "lon": -77.2, "radius_nm": 3.0}])
    monkeypatch.setattr(la, "get_active_entries", lambda entry_type=None, cached=False: [])
    la._process_acars_message(POS_IN_TFR)
    assert _rows()[-1] == {"msg_type": "POS", "acars_flags": "position_report", "watchlist_hit": 0}


def test_hold_near_tfr_and_future_tfr_ignored(env):
    _tfr([{"name": "A", "lat": 38.5, "lon": -77.2, "radius_nm": 1.0}])          # ~21 nm south
    hold = {"tail": "N0TEST9", "flight": "TST909", "label": "5Z", "block_id": "1",
            "text": "HOLDING AT FIX01 POS N38512W077123"}
    la._process_acars_message(hold)
    assert "tfr_notam_mention" in _rows()[-1]["acars_flags"].split(",")
    la._tfr_areas_cache.update(at=-1e18, areas=[])
    _tfr([{"name": "A", "lat": 38.5, "lon": -77.2, "radius_nm": 1.0}], start=time.time() + 3600)
    la._process_acars_message(hold)
    assert "tfr_notam_mention" not in _rows()[-1]["acars_flags"].split(",")  # not in effect yet


def test_tfr_areas_read_through_cache(env, monkeypatch):
    calls = []
    real = db.get_active_tfrs
    monkeypatch.setattr(db, "get_active_tfrs", lambda: calls.append(1) or real())
    for _ in range(5):
        la._process_acars_message(POS_IN_TFR)
    assert len(calls) == 1


# ── flag pushes for watched flights (2026-10-18) ─────────────────────────────

DIVERT = {"tail": "N0TEST1", "flight": "TST101", "label": "5Z", "block_id": "3",
          "text": "WILL DIVERT TO KIAD MEDICAL"}


def _flag_hits(env, trigger):
    return [h for h in env if h[1]["watchlist_trigger"] == trigger]


def test_tfr_area_push_non_vip_p4_with_window_no_position(env):
    _tfr([{"name": "A", "lat": 38.85, "lon": -77.2, "radius_nm": 3.0}])
    la._process_acars_message(POS_IN_TFR)
    (eid, detail, priority, summary), = _flag_hits(env, "acars_tfr_area")
    assert priority == 4 and detail["tfr_id"] == "6/9001"
    assert summary.startswith("TST101 inside active TFR 6/9001\nActive ") and " ET " in summary
    assert "38." not in summary and "77." not in summary and "N0TEST1" not in summary
    assert "msg_text" not in detail and "tail" not in detail and "icao_hex" not in detail


def test_vip_listed_pushes_p5(env, monkeypatch, tmp_path):
    import shared.vip_listed as vl
    d = tmp_path / "wl"
    d.mkdir()
    (d / "permanent_flights.json").write_text(json.dumps({"watchlist": [
        {"id": "wl-synth-1", "identifier": "TST101", "route_name": "VC-25B", "notes": ""}]}))
    monkeypatch.setattr(vl, "PERMANENT_WATCHLIST_DIR", d)
    _tfr([{"name": "A", "lat": 38.85, "lon": -77.2, "radius_nm": 3.0}])
    la._process_acars_message(POS_IN_TFR)
    la._process_acars_message(DIVERT)
    assert [h[2] for h in _flag_hits(env, "acars_tfr_area")] == [5]
    assert [h[2] for h in _flag_hits(env, "acars_diversion")] == [5]


def test_flag_push_dedup_on_content(env):
    _tfr([{"name": "A", "lat": 38.85, "lon": -77.2, "radius_nm": 3.0}])
    for _ in range(4):
        la._process_acars_message(POS_IN_TFR)
        la._process_acars_message(DIVERT)
    assert len(_flag_hits(env, "acars_tfr_area")) == 1
    dv = _flag_hits(env, "acars_diversion")
    assert len(dv) == 1 and dv[0][1]["new_destination"] == "KIAD"
    assert dv[0][3] == "TST101 ACARS: diverting to KIAD"
    la._process_acars_message(dict(DIVERT, text="NOW DIVERTING TO KBWI"))
    la._process_acars_message(dict(DIVERT, text="DIVERT DUE WX"))
    assert [h[1]["new_destination"] for h in _flag_hits(env, "acars_diversion")] == ["KIAD", "KBWI", "unstated"]


def test_no_flag_push_when_not_watched(env, monkeypatch):
    _tfr([{"name": "A", "lat": 38.85, "lon": -77.2, "radius_nm": 3.0}])
    monkeypatch.setattr(la, "get_active_entries", lambda entry_type=None, cached=False: [])
    la._process_acars_message(POS_IN_TFR)
    la._process_acars_message(DIVERT)
    assert env == []
    assert "diversion" in _rows()[-1]["acars_flags"]          # still flagged on the row


def test_oooi_unaffected_by_flag_pushes(env):
    off_divert = {"vdl2": {**VDL2_OFF["vdl2"], "avlc": {**VDL2_OFF["vdl2"]["avlc"], "acars": {
        **VDL2_OFF["vdl2"]["avlc"]["acars"], "msg_text": "OFF 1912 DIVERT TO KIAD"}}}}
    la._process_acars_message(off_divert)                     # phase writers raise if touched (fixture)
    assert sorted(h[1]["watchlist_trigger"] for h in env) == ["acars_diversion", "acars_off"]


def test_vc25c_is_vip_listed(monkeypatch, tmp_path):
    # 2026-10-19 (operator): VC-25C joins the VIP-listed types.
    import shared.vip_listed as vl
    assert "VC25C" in vl.VIP_ROUTE_NAMES
    for route in ("VC-25C", "vc25c", "VC 25C", "VC-25B/VC-25C"):
        assert vl.is_vip_record({"route_name": route}), route
    monkeypatch.setattr(vl, "PERMANENT_WATCHLIST_DIR", tmp_path / "none")
    vl._cache.update(mtime=None, by_id={})
    assert vl.is_vip_listed({"id": "x", "tier": "permanent", "route_name": "VC-25C"})


def test_vip_listed_predicate(monkeypatch, tmp_path):
    import shared.vip_listed as vl
    rec = vl.is_vip_record
    for route in ("VC-25A", "VC-25B", "C-32", "C-37", "C-40", "C-40C", "E-4B", "E4B/E4C", "vc 25a"):
        assert rec({"route_name": route}), route
    for notes in ("AF1 backup", "flies as SAM41", "Nightwatch", "af2"):
        assert rec({"route_name": "757", "notes": notes}), notes
    for r in ({"route_name": "British Airways LHR-IAD"}, {"route_name": "VC-25D"}, {"notes": "SAMPLE"}, None):
        assert not rec(r)
    monkeypatch.setattr(vl, "PERMANENT_WATCHLIST_DIR", tmp_path / "none")
    vl._cache.update(mtime=None, by_id={})
    assert vl.is_vip_listed({"id": "x", "tier": "permanent", "route_name": "C-40C"})       # DB copy fallback
    assert not vl.is_vip_listed({"id": "x", "tier": "temporary", "route_name": "C-40C"})
    d = tmp_path / "wl"
    d.mkdir()
    (d / "permanent_flights.json").write_text(json.dumps({"watchlist": [{"id": "a", "route_name": "E-4B"}]}))
    monkeypatch.setattr(vl, "PERMANENT_WATCHLIST_DIR", d)
    assert vl.is_vip_listed({"id": "a"}) and not vl.is_vip_listed({"id": "b", "tier": "permanent",
                                                                   "route_name": "C-32"})  # the file decides
