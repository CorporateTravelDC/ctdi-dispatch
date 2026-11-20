"""
tests/poller/test_fdps_predeparture.py -- 2026-11-18 (ASH4006 KSAV-KIAD): a watched
leg with no FDPS plan of its own by departure time, or airborne without one, is
named once per state; another day's plan never counts and nothing is bound.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from common import db  # noqa: E402
from common import fdps_predeparture as g  # noqa: E402
from common import push_policy_registry as reg  # noqa: E402
import poller.main as pm  # noqa: E402
import shared.watchlist as wl  # noqa: E402


def ep(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


ENTRY = {"id": "wl-flight-ash4006-20261118", "identifier": "ASH4006", "origin": "KSAV",
         "destination": "KIAD", "oooi_phase": "pre_departure", "entry_type": "flight"}
# yesterday's leg: the only FDPS row on file before departure (arrived 11-17 20:00Z)
YESTERDAY = {"flight_id": "y", "origin": "KSAV", "destination": "KIAD", "status": "active",
             "departure_time": None, "arrival_time": ep("2026-11-17T20:00:48Z"),
             "updated_at": ep("2026-11-17T19:50:56Z")}
TODAY = {**YESTERDAY, "flight_id": "t", "arrival_time": ep("2026-11-18T20:06:20Z"),
         "updated_at": ep("2026-11-18T18:58:00Z")}
TFMS_PLANNED = {"original_departure": "2026-11-18T18:35:00Z", "airline_out_time": "2026-11-18T18:35:00Z",
                "airline_off_time": None}
TFMS_OFF = {**TFMS_PLANNED, "airline_off_time": "2026-11-18T18:51:00Z", "flight_status": "ACTIVE"}


def test_far_from_departure_says_nothing():
    assert g.assess(ENTRY, YESTERDAY, TFMS_PLANNED, ep("2026-11-18T16:00:00Z")) is None


def test_yesterdays_plan_inside_the_lead_window_is_missing_predeparture():
    v = g.assess(ENTRY, YESTERDAY, TFMS_PLANNED, ep("2026-11-18T18:00:00Z"))
    assert v["state"] == "plan_missing_predeparture"
    assert v["newest_plan_rejected"] == "prior_day_occurrence"
    assert "18:35Z (13:35 ET)" in v["summary"]


def test_no_plan_at_all_also_counts():
    assert g.assess(ENTRY, None, TFMS_PLANNED, ep("2026-11-18T18:10:00Z"))["state"] == "plan_missing_predeparture"


def test_tfms_off_without_a_plan_is_airborne_without_plan():
    v = g.assess(ENTRY, YESTERDAY, TFMS_OFF, ep("2026-11-18T18:55:00Z"))
    assert v["state"] == "airborne_without_plan"
    assert "OFF 18:51Z (13:51 ET)" in v["summary"]


def test_passed_off_estimate_on_a_planned_row_is_not_airborne():
    # 2026-11-19 UAL376 KAUS-KIAD: TFMS OFF 23:33Z passed, flightStatus PLANNED, aircraft at the stand.
    late = {**TFMS_OFF, "flight_status": "PLANNED"}
    v = g.assess(ENTRY, YESTERDAY, late, ep("2026-11-18T18:55:00Z"))
    assert v["state"] == "plan_missing_predeparture"
    assert "airborne" not in v["summary"]
    assert g.assess(ENTRY, YESTERDAY, {**late, "flight_status": None},
                    ep("2026-11-18T18:55:00Z"))["state"] == "plan_missing_predeparture"


def test_completed_row_still_counts_as_airborne():
    v = g.assess(ENTRY, YESTERDAY, {**TFMS_OFF, "flight_status": "COMPLETED"}, ep("2026-11-18T18:55:00Z"))
    assert v["state"] == "airborne_without_plan"


def test_watch_phase_off_without_a_plan_is_airborne_without_plan():
    e = {**ENTRY, "oooi_phase": "off"}
    assert g.assess(e, None, None, ep("2026-11-18T18:55:00Z"))["state"] == "airborne_without_plan"


def test_same_day_plan_clears_it():
    assert g.assess(ENTRY, TODAY, TFMS_OFF, ep("2026-11-18T19:00:00Z")) is None


def test_watch_schedule_wins_over_tfms_and_another_days_tfms_is_ignored():
    e = {**ENTRY, "scheduled_departure": "2026-11-18T21:00:00Z"}
    assert g.assess(e, None, TFMS_PLANNED, ep("2026-11-18T18:40:00Z")) is None
    old_tfms = {"original_departure": "2026-11-17T18:32:00Z", "airline_off_time": "2026-11-17T18:51:00Z"}
    assert g.assess(ENTRY, YESTERDAY, old_tfms, ep("2026-11-18T18:40:00Z")) is None


def test_unknown_departure_says_nothing_until_airborne():
    assert g.assess(ENTRY, YESTERDAY, None, ep("2026-11-18T18:40:00Z")) is None


def test_kind_is_registered_as_status_keyed_on_state():
    rule = reg.KINDS[("aviation", "fdps_plan_missing_predeparture")]
    assert rule.cls == "status" and rule.identity == ("state",)


@pytest.fixture
def rig(monkeypatch):
    hits = []
    monkeypatch.setattr(wl, "watchlist_event_hit", lambda eid, s, d, priority=3: hits.append((eid, s, d, priority)))
    monkeypatch.setattr(db, "get_flight_plan_by_callsign", lambda cs, *a, **k: YESTERDAY if cs == "ASH4006" else None)
    monkeypatch.setattr(db, "get_recent_flight_ooooi", lambda cs, *a, **k: TFMS_OFF if cs == "ASH4006" else None)
    monkeypatch.setattr(pm.time, "time", lambda: ep("2026-11-18T18:55:00Z"))
    return hits


def test_poller_hook_pushes_through_the_watch_event_path(rig):
    pm._check_fdps_predeparture(ENTRY, "ASH4006")
    (eid, summary, detail, prio), = rig
    assert eid == ENTRY["id"] and prio == 4
    assert detail["watchlist_trigger"] == "fdps_plan_missing_predeparture"
    assert detail["state"] == "airborne_without_plan"
    assert "hex" not in detail and "registration" not in detail


def test_poller_hook_swallows_errors(rig, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "get_flight_plan_by_callsign", boom)
    pm._check_fdps_predeparture(ENTRY, "ASH4006")
    assert rig == []


def test_non_flight_entries_are_ignored(rig):
    pm._check_fdps_predeparture({**ENTRY, "entry_type": "train"}, "ASH4006")
    assert rig == []


# ── 2026-11-18 fleet-wide check: every codeshare identity, same occurrence wins ──────────

TFMS_ROUTE_OFF = {**TFMS_OFF, "origin": "KSAV", "destination": "KIAD"}
UAL_ENTRY = {**ENTRY, "id": "wl-flight-ual4006-20261118", "identifier": "UAL4006"}


@pytest.fixture
def nomap(monkeypatch):
    """codeshare_map with no row for this flight (as on 11-18: no UAL->ASH rows)."""
    monkeypatch.setattr(db, "get_codeshare_mapping_by_marketing", lambda *a, **k: [])
    monkeypatch.setattr(db, "get_codeshare_mapping_by_operating", lambda *a, **k: [])


def test_partner_table_matches_the_seed_script():
    import importlib.util
    p = Path(__file__).resolve().parents[2] / "scripts" / "seed-dc-codeshare.py"
    spec = importlib.util.spec_from_file_location("seed_dc_codeshare", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    inv: dict[str, set] = {}
    for op, mk in m.EXCLUSIVE_REGIONAL_MARKETING.items():
        if op != "JZA":                      # AC 8000+n numbering: left to the seed
            inv.setdefault(mk, set()).add(op)
    for op, desc in m.AMBIGUOUS_REGIONALS.items():
        for mk in ("AAL", "DAL", "UAL", "ASA"):
            if mk in desc:
                inv.setdefault(mk, set()).add(op)
    assert {k: set(v) for k, v in g.REGIONAL_PARTNERS.items()} == inv


def test_marketing_entry_with_no_map_row_finds_the_operating_leg_via_partners(nomap):
    plans = {"ASH4006": YESTERDAY}
    tfms = {"ASH4006": TFMS_ROUTE_OFF}
    r = g.gather(UAL_ENTRY, "UAL4006", ep("2026-11-18T18:55:00Z"), plans.get, tfms.get)
    assert "ASH4006" in r["checked"]
    assert ("ASH4006", "partner_fallback") in r["linked"]
    v = g.assess(UAL_ENTRY, r["plan"], r["tfms"], ep("2026-11-18T18:55:00Z"))
    assert v["state"] == "airborne_without_plan"


def test_marketing_entry_partner_same_day_plan_clears_it(nomap):
    plans = {"ASH4006": {**TODAY, "origin": "KSAV", "destination": "KIAD"}}
    tfms = {"ASH4006": TFMS_ROUTE_OFF}
    r = g.gather(UAL_ENTRY, "UAL4006", ep("2026-11-18T19:00:00Z"), plans.get, tfms.get)
    assert g.assess(UAL_ENTRY, r["plan"], r["tfms"], ep("2026-11-18T19:00:00Z")) is None


def test_partner_candidate_on_another_route_is_rejected(nomap):
    plans = {"SKW4006": {**TODAY, "origin": "KTUL", "destination": "KSLC"}}
    tfms = {"SKW4006": {**TFMS_OFF, "origin": "KTUL", "destination": "KSLC"}}
    r = g.gather(UAL_ENTRY, "UAL4006", ep("2026-11-18T18:55:00Z"), plans.get, tfms.get)
    assert r["plan"] is None and r["tfms"] is None and r["linked"] == []


def test_partner_candidate_from_another_day_is_rejected(nomap):
    old = {**TFMS_ROUTE_OFF, "original_departure": "2026-11-17T18:35:00Z",
           "airline_out_time": "2026-11-17T18:35:00Z", "airline_off_time": "2026-11-17T18:51:00Z"}
    r = g.gather(UAL_ENTRY, "UAL4006", ep("2026-11-18T18:55:00Z"), {"ASH4006": YESTERDAY}.get,
                 {"ASH4006": old}.get)
    assert r["linked"] == []


def test_stale_marketing_row_does_not_hide_the_operating_same_day_plan(monkeypatch):
    monkeypatch.setattr(db, "get_codeshare_mapping_by_marketing",
                        lambda c, n, *a, **k: [{"operating_carrier": "ASH", "operating_flight_num": "4006",
                                                "origin": "KSAV", "destination": "KIAD"}] if c == "UAL" else [])
    monkeypatch.setattr(db, "get_codeshare_mapping_by_operating", lambda *a, **k: [])
    plans = {"UAL4006": YESTERDAY, "ASH4006": TODAY}      # marketing first, stale
    r = g.gather(UAL_ENTRY, "UAL4006", ep("2026-11-18T19:00:00Z"), plans.get, {}.get)
    assert r["plan"] is TODAY
    assert g.assess(UAL_ENTRY, r["plan"], r["tfms"], ep("2026-11-18T19:00:00Z")) is None


def test_operating_entry_links_the_marketing_callsign_both_ways(monkeypatch):
    monkeypatch.setattr(db, "get_codeshare_mapping_by_marketing", lambda *a, **k: [])
    monkeypatch.setattr(db, "get_codeshare_mapping_by_operating",
                        lambda c, n, *a, **k: [{"marketing_carrier": "UAL", "marketing_flight_num": "4006",
                                                "origin": "KSAV", "destination": "KIAD"}] if c == "ASH" else [])
    ids = g.candidate_identities("ASH4006", "KSAV", "KIAD")
    assert ("UAL4006", "codeshare_reverse") in ids
    # and without a map row, the partner inverse still offers it as a (route-checked) candidate
    monkeypatch.setattr(db, "get_codeshare_mapping_by_operating", lambda *a, **k: [])
    assert ("UAL4006", "partner_fallback_marketing") in g.candidate_identities("ASH4006", "KSAV", "KIAD")


def test_poller_hook_reports_the_linked_identity(monkeypatch, nomap):
    hits = []
    monkeypatch.setattr(wl, "watchlist_event_hit", lambda eid, s, d, priority=3: hits.append(d))
    monkeypatch.setattr(db, "get_flight_plan_by_callsign", lambda cs, *a, **k: YESTERDAY if cs == "ASH4006" else None)
    monkeypatch.setattr(db, "get_recent_flight_ooooi", lambda cs, *a, **k: TFMS_ROUTE_OFF if cs == "ASH4006" else None)
    monkeypatch.setattr(pm.time, "time", lambda: ep("2026-11-18T18:55:00Z"))
    pm._check_fdps_predeparture(UAL_ENTRY, "UAL4006")
    (d,) = hits
    assert d["state"] == "airborne_without_plan"
    assert "ASH4006 (partner_fallback)" in d["linked_identities"]
    assert "hex" not in d and "registration" not in d
