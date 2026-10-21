"""
2026-10-20 (bellwether batch) -- the public-number lookaside in codeshare_map
(pg_schema/0082, SQLite twin db.init_db_v51) and its two learners
(common.callsign_lookaside): FIDS leg matches and SWIM route continuity.

Seeded links are the two real ones verified 2026-10-19 (EK231 = UAE3MX, LH414 =
DLH3Y). Every other number below is synthetic (EK9871, UAE9QZ, UAE9QW ...).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from common import callsign_lookaside as cl
from common import db
from common import flight_instance as fi

MIGRATION = (Path(__file__).resolve().parents[2] / "src" / "common" / "pg_schema"
             / "0082_codeshare_atc_callsign_lookaside.sql")


@pytest.fixture
def fresh(monkeypatch):
    db.init_db_all()
    fi.clear_alias_cache()
    from common import gate_assignments as ga
    monkeypatch.setattr(ga, "ladd_blocked", lambda idents=(), hexes=(): False)
    cl._discovery_last.clear()
    yield
    fi.clear_alias_cache()


def _fe(flight_id, callsign, origin, dest, arr_ep, status="active", upd=None):
    airline, num = callsign[:3], callsign[3:]
    with db.conn() as c:
        c.execute("INSERT INTO flight_events (flight_id, airline, flight_num, origin, destination, "
                  "arrival_time, status, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (flight_id, airline, num, origin, dest, arr_ep, status, upd or arr_ep - 3600))


def _ep(d: date, hh: int, mm: int) -> float:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone.utc).timestamp()


# ── migration + twin ─────────────────────────────────────────────────────────

def test_migration_adds_columns_check_and_the_two_seed_rows():
    sql = MIGRATION.read_text()
    assert "SET LOCAL lock_timeout" in sql
    assert re.search(r"ADD COLUMN IF NOT EXISTS relation TEXT NOT NULL DEFAULT 'codeshare'\s+"
                     r"CHECK \(relation IN \('codeshare', 'atc_callsign'\)\)", sql)
    for col in ("season TEXT", "retired_at TEXT", "superseded_by INTEGER"):
        assert f"ADD COLUMN IF NOT EXISTS {col}" in sql
    assert "ALTER TABLE watchlist_entries ADD COLUMN IF NOT EXISTS alert_topic TEXT" in sql
    rows = set(re.findall(r"\('(\w+)', '(\w+)', '(\w+)', '(\w+)',\s*'(\w+)', '(\w+)'\)", sql))
    want = set()
    for s in db.ATC_CALLSIGN_SEED:
        pub, atc = db.split_public_number(s["public"]), db.split_atc_callsign(s["atc"])
        want.add((pub[0], pub[1], atc[0], atc[1], s["origin"], s["destination"]))
        assert s["season"] == "W26-pending" and s["source"] == "fids_route_unique"
    assert rows == want
    assert "'W26-pending'" in sql and "'fids_route_unique'" in sql
    assert not re.search(r"^\s*DELETE\b", sql, re.IGNORECASE | re.MULTILINE)   # history is never deleted


def test_twin_seeds_once_and_resolves_the_current_callsign(fresh):
    assert db.get_current_atc_callsign("EK231", "OMDB", "KIAD") == "UAE3MX"
    assert db.get_current_atc_callsign("LH414", "EDDM", "IAD") == "DLH3Y"
    assert db.get_current_atc_callsign("EK231", "KIAD", "OMDB") is None        # other direction
    db.init_db_v51()
    assert len(db.get_atc_callsign_links("EK231")) == 1


def test_split_helpers():
    assert db.split_public_number("EK231") == ("UAE", "231")
    assert db.split_public_number("KE031") == ("KAL", "31")
    assert db.split_atc_callsign("UAE3MX") == ("UAE", "3MX")
    assert db.split_atc_callsign("KAL031") == ("KAL", "31")
    assert db.split_atc_callsign("N" + "123AB") is None


# ── links: confirm, relink, history ──────────────────────────────────────────

def test_confirm_relink_keeps_history_and_readers_skip_retired(fresh):
    a = db.upsert_atc_callsign_link("EK9871", "UAE9QZ", "OMDB", "KIAD", "operator")
    assert a["action"] == "inserted"
    b = db.upsert_atc_callsign_link("EK9871", "UAE9QZ", "OMDB", "KIAD", "fids_leg_match", season="S26")
    assert b == {"id": a["id"], "action": "confirmed", "retired_id": None, "old_callsign": None}
    c = db.upsert_atc_callsign_link("EK9871", "UAE9QW", "OMDB", "KIAD", "route_continuity", season="W26")
    assert c["action"] == "relinked" and c["retired_id"] == a["id"] and c["old_callsign"] == "UAE9QZ"
    hist = {r["id"]: r for r in db.get_atc_callsign_links("EK9871", include_retired=True)}
    assert hist[a["id"]]["retired_at"] and hist[a["id"]]["superseded_by"] == c["id"]
    assert hist[a["id"]]["confidence"] == 2 and hist[a["id"]]["season"] == "S26"
    assert hist[c["id"]]["retired_at"] is None and hist[c["id"]]["source"] == "route_continuity"
    current = db.get_codeshare_mapping_by_marketing("UAE", "9871")
    assert [r["operating_flight_num"] for r in current] == ["9QW"]
    assert len(db.get_codeshare_mapping_by_marketing("UAE", "9871", include_retired=True)) == 2
    assert db.get_current_atc_callsign("EK9871", "OMDB", "KIAD") == "UAE9QW"


def test_codeshare_upsert_never_touches_an_atc_link(fresh):
    a = db.upsert_atc_callsign_link("EK9871", "UAE9QZ", "OMDB", "KIAD", "operator")
    db.upsert_codeshare_mapping("UAE", "9871", "UAE", None, "OMDB", "KIAD", "test")
    rows = {r["id"]: r for r in db.get_codeshare_mapping_by_marketing("UAE", "9871")}
    assert rows[a["id"]]["confidence"] == 1 and rows[a["id"]]["relation"] == "atc_callsign"
    assert any(r["relation"] == "codeshare" for r in rows.values())


def test_link_refusals(fresh):
    with pytest.raises(ValueError):
        db.upsert_atc_callsign_link("EK9871", "UAE9QZ", "OMDB", "KIAD", "fids_status")
    assert db.upsert_atc_callsign_link("EK9871", "UAE9871", "OMDB", "KIAD", "operator") is None


def test_iata_season_boundaries():
    assert cl.iata_season(date(2026, 10, 24)) == "S26"
    assert cl.iata_season(date(2026, 10, 25)) == "W26"
    assert cl.iata_season(date(2026, 3, 28)) == "W25"
    assert cl.iata_season(date(2026, 3, 29)) == "S26"


# ── FIDS learner ─────────────────────────────────────────────────────────────

def _inst(callsign, origin="OMDB", dest="KIAD", arr=None, sched_date="2026-10-20"):
    c, n = fi.parse_flight_ident(callsign)
    return SimpleNamespace(key=fi.FlightInstanceKey(c, n, fi.norm_airport(origin), fi.norm_airport(dest),
                                                    sched_date),
                           sched_arr=arr, est_arr=arr, sched_dep=None, hex=None)


def test_fids_window_match_on_a_verified_leg_records_the_link(fresh):
    entry = {"id": "perm-flight-ek9871-test", "identifier": "EK9871"}
    res = cl.learn_from_fids(entry, "EK9871", airport="IAD", side="arrival", iata="EK", flight_num="9871",
                             other="OMDB", inst=_inst("UAE9QZ"), row={"gate": "B41", "leg_match": "route_watch"},
                             sched_date="2026-10-20")
    assert res["action"] == "inserted"
    link = db.get_atc_callsign_links("EK9871")[0]
    assert (link["operating_carrier"], link["operating_flight_num"], link["source"]) == ("UAE", "9QZ", "fids_leg_match")
    # callsigns and the route only: no gate, time, status or tail landed in the row
    assert "B41" not in repr(link)


def test_fids_route_only_match_on_a_verified_leg_is_not_evidence(fresh):
    entry = {"id": "perm-flight-ek9871-test", "identifier": "EK9871"}
    assert cl.learn_from_fids(entry, "EK9871", airport="IAD", side="arrival", iata="EK", flight_num="9871",
                              other="OMDB", inst=_inst("UAE9QZ"), row={"leg_match": "route_only"},
                              sched_date="2026-10-20") is None


def test_fids_codeshare_when_the_carrier_differs(fresh):
    entry = {"id": "wl-flight-ua9520-test", "identifier": "UA9520"}
    res = cl.learn_from_fids(entry, "UA9520", airport="IAD", side="arrival", iata="UA", flight_num="9520",
                             other="ORD", inst=_inst("GJS9520", "KORD", "KIAD"),
                             row={"leg_match": "route_watch"}, sched_date="2026-10-20")
    assert res["action"] == "codeshare"
    rows = db.get_codeshare_mapping_by_marketing("UAL", "9520")
    assert rows[0]["relation"] == "codeshare" and rows[0]["operating_carrier"] == "GJS"


def test_fids_discovery_route_unique(fresh, monkeypatch):
    from common import airport_fids
    now = datetime.now(timezone.utc).timestamp()
    _fe("G-SYN-A", "UAE9QZ", "OMDB", "KIAD", now + 3600, upd=now - 600)
    monkeypatch.setattr(airport_fids, "get_data", lambda a, force=False: {"arrivals": [
        {"IATA": "EK", "flightnumber": "9871"}]})
    monkeypatch.setattr(airport_fids, "match_leg", lambda *a: {"gate": "B41", "leg_match": "route_watch"})
    entry = {"id": "perm-flight-ek9871-test", "identifier": "EK9871"}
    res = cl.learn_from_fids(entry, "EK9871", airport="IAD", side="arrival", iata="EK", flight_num="9871",
                             other="OMDB", inst=None, row=None, sched_date=None)
    assert res["action"] == "inserted"
    assert db.get_atc_callsign_links("EK9871")[0]["source"] == "fids_route_unique"
    # the 6 h discovery budget per watch side
    db.retire_atc_callsign_link(res["id"])
    assert cl.learn_from_fids(entry, "EK9871", airport="IAD", side="arrival", iata="EK", flight_num="9871",
                              other="OMDB", inst=None, row=None, sched_date=None) is None


def test_fids_discovery_ambiguous_or_own_number_learns_nothing(fresh, monkeypatch):
    from common import airport_fids
    now = datetime.now(timezone.utc).timestamp()
    _fe("G-SYN-A", "UAE9QZ", "OMDB", "KIAD", now + 3600, upd=now - 600)
    _fe("G-SYN-B", "UAE9QY", "OMDB", "KIAD", now + 7200, upd=now - 600)
    monkeypatch.setattr(airport_fids, "get_data", lambda a, force=False: {"arrivals": []})
    monkeypatch.setattr(airport_fids, "match_leg", lambda *a: {"leg_match": "route_watch"})
    entry = {"id": "perm-flight-ek9871-test", "identifier": "EK9871"}
    kw = dict(airport="IAD", side="arrival", iata="EK", flight_num="9871", other="OMDB",
              inst=None, row=None, sched_date=None)
    assert cl.learn_from_fids(entry, "EK9871", **kw) is None                 # two legs pick the row
    cl._discovery_last.clear()
    _fe("G-SYN-C", "UAE9871", "OMDB", "KIAD", now + 3600, upd=now - 600)     # filed under its own number
    assert cl.learn_from_fids(entry, "EK9871", **kw) is None
    assert db.get_atc_callsign_links("EK9871") == []


def test_fids_discovery_skips_another_public_flight_on_the_board(fresh, monkeypatch):
    from common import airport_fids
    now = datetime.now(timezone.utc).timestamp()
    _fe("G-SYN-A", "UAE9875", "OMDB", "KIAD", now + 3600, upd=now - 600)     # EK9875's own leg
    monkeypatch.setattr(airport_fids, "get_data", lambda a, force=False: {"arrivals": [
        {"IATA": "EK", "flightnumber": "9871"}, {"IATA": "EK", "flightnumber": "9875"}]})
    monkeypatch.setattr(airport_fids, "match_leg", lambda *a: {"leg_match": "route_watch"})
    entry = {"id": "perm-flight-ek9871-test", "identifier": "EK9871"}
    assert cl.learn_from_fids(entry, "EK9871", airport="IAD", side="arrival", iata="EK", flight_num="9871",
                              other="OMDB", inst=None, row=None, sched_date=None) is None


# ── route continuity ─────────────────────────────────────────────────────────

LINK = {"id": 1, "marketing_carrier": "UAE", "marketing_flight_num": "9871", "operating_carrier": "UAE",
        "operating_flight_num": "9QZ", "origin": "OMDB", "destination": "KIAD"}
TODAY = date(2026, 10, 27)


def _obs(cs, days_back, hh, mm):
    return (cs, (TODAY - timedelta(days=days_back)).isoformat(), hh * 60 + mm)


def test_continuity_decision_rules():
    base = [_obs("UAE9QZ", d, 12, 30) for d in range(3, 10)]
    new_both = [_obs("UAE9QW", 1, 12, 45), _obs("UAE9QW", 2, 12, 20)]
    assert cl.continuity_decision(LINK, base + new_both, TODAY)["new_callsign"] == "UAE9QW"
    # old callsign still flew on one gap day
    assert cl.continuity_decision(LINK, base + new_both + [_obs("UAE9QZ", 1, 12, 30)], TODAY) is None
    # the new one only on one day
    assert cl.continuity_decision(LINK, base + new_both[:1], TODAY) is None
    # outside the +/-30 min slot
    assert cl.continuity_decision(LINK, base + [_obs("UAE9QW", 1, 14, 0), _obs("UAE9QW", 2, 14, 0)], TODAY) is None
    # two new candidates: ambiguous
    two = new_both + [_obs("UAE9QV", 1, 12, 35), _obs("UAE9QV", 2, 12, 35)]
    assert cl.continuity_decision(LINK, base + two, TODAY) is None
    # a callsign already flying the route in the baseline is not new
    known = base + [_obs("UAE9QW", 5, 12, 40)] + new_both
    assert cl.continuity_decision(LINK, known, TODAY) is None
    # midnight wrap: 23:50 typical, 00:10 new is 20 min away
    wrap = [_obs("UAE9QZ", d, 23, 50) for d in range(3, 8)] + [_obs("UAE9QW", 1, 0, 10), _obs("UAE9QW", 2, 0, 5)]
    assert cl.continuity_decision(LINK, wrap, TODAY)["new_callsign"] == "UAE9QW"


def test_relink_by_route_continuity_end_to_end_pushes_once_on_bellwether_alerts(fresh, monkeypatch, tmp_path):
    import shared.vip_listed as vl
    import shared.watchlist as wl
    monkeypatch.setattr(vl, "PERMANENT_WATCHLIST_DIR", tmp_path / "none")
    vl._cache.update(mtime=None, by_id={})
    link = db.upsert_atc_callsign_link("EK9871", "UAE9QZ", "OMDB", "KIAD", "operator")
    for d in range(3, 10):
        day = TODAY - timedelta(days=d)
        _fe(f"G-OLD-{d}", "UAE9QZ", "OMDB", "KIAD", _ep(day, 12, 30))
    for d in (1, 2):
        day = TODAY - timedelta(days=d)
        _fe(f"G-NEW-{d}", "UAE9QW", "OMDB", "KIAD", _ep(day, 12, 40))
    _fe("G-OTHER", "UAE9QW", "OMDB", "KJFK", _ep(TODAY - timedelta(days=5), 12, 40))   # other route
    entry = {"id": "perm-flight-ek9871-test", "entry_type": "flight", "tier": "permanent",
             "identifier": "EK9871", "origin": "OMDB", "destination": "KIAD",
             "added_at": "2026-10-20T00:00:00Z", "added_by": "operator", "alert_topic": "bellwether-alerts"}
    db.upsert_watchlist_entry(entry)
    plain = {**entry, "id": "wl-flight-ek9871-plain", "tier": "transient", "alert_topic": None}
    db.upsert_watchlist_entry(plain)
    pushes = []
    monkeypatch.setattr(wl, "watchlist_event_hit",
                        lambda eid, summary, detail, priority=3: pushes.append((eid, summary, detail, priority)))
    stats = cl.relink_by_route_continuity(TODAY)
    assert stats["relinked"] == 1 and stats["pushed"] == 1 and stats["errors"] == 0
    assert db.get_current_atc_callsign("EK9871", "OMDB", "KIAD") == "UAE9QW"
    old = next(r for r in db.get_atc_callsign_links("EK9871", include_retired=True) if r["id"] == link["id"])
    assert old["retired_at"] and old["superseded_by"]
    new = db.get_atc_callsign_links("EK9871")[0]
    assert new["source"] == "route_continuity" and new["season"] == "W26"
    (eid, summary, detail, prio), = pushes
    assert eid == entry["id"] and prio == 3 and "UAE9QZ -> UAE9QW" in summary
    assert detail["watchlist_trigger"] == "atc_callsign_relink"
    from shared.watch_topic import topic_for_entry
    assert topic_for_entry(entry) == "bellwether-alerts"
    # a second run the same day finds nothing to move
    assert cl.relink_by_route_continuity(TODAY)["relinked"] == 0


# ── wiring ───────────────────────────────────────────────────────────────────

def test_poller_fids_step_feeds_the_learner_with_the_same_window(fresh, monkeypatch):
    import poller.main as pm
    from common import airport_fids, gate_assignments as ga
    inst = _inst("UAE9QZ", arr=1_800_000_000.0)
    monkeypatch.setattr(ga, "leg_instance", lambda key: inst)
    monkeypatch.setattr(ga, "record", lambda *a, **k: None)
    monkeypatch.setattr(airport_fids, "get_payload_hash", lambda a: "h")
    windows, learned = [], []
    monkeypatch.setattr(airport_fids, "match_leg",
                        lambda airport, side, iata, num, other, window: windows.append(window)
                        or {"gate": "B41", "terminal": None, "baggage": None, "leg_match": "route_watch"})
    monkeypatch.setattr(cl, "learn_from_fids", lambda entry, ident, **kw: learned.append((ident, kw)))
    key = fi.FlightInstanceKey("UAE", "9QZ", "OMDB", "IAD", "2026-10-20")
    pm._check_flight_fids({"id": "perm-flight-ek9871-test", "identifier": "EK9871"}, "EK9871", key)
    assert windows == [(1_800_000_000.0 - 7200, 1_800_000_000.0 + 8 * 3600)]
    (ident, kw), = learned
    assert ident == "EK9871" and kw["iata"] == "EK" and kw["flight_num"] == "9871" and kw["inst"] is inst


def test_learner_reads_no_fids_time_status_or_tail():
    src = Path(cl.__file__).read_text()
    code = "\n".join(ln.split("#", 1)[0] for ln in src.split('"""', 2)[2].splitlines())
    assert "_published_epoch" not in src
    for key in ("estimated", "scheduled", "status_text", "remarks", "tail", "registration"):
        assert f'"{key}"' not in code and f"'{key}'" not in code


def test_daily_flight_cleanup_runs_the_continuity_relink_and_survives_its_failure(monkeypatch):
    from poller.skills import flight_events_cleanup as fec
    monkeypatch.setattr(cl, "relink_by_route_continuity",
                        lambda today=None: {"links": 2, "relinked": 0, "pushed": 0, "errors": 0})
    assert fec.run_lookaside_continuity()["links"] == 2

    def boom(today=None):
        raise RuntimeError("db down")
    monkeypatch.setattr(cl, "relink_by_route_continuity", boom)
    assert fec.run_lookaside_continuity() is None
    main = Path(fec.__file__).read_text().split('if __name__ == "__main__":', 1)[1]
    assert main.index("run_lookaside_continuity()") < main.index("    run()")
