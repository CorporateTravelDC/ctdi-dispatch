"""
2026-10-13 -- common.flight_instance (flight-instance resolver).

The operator cases (2026-10-11/12) these pin:
  - AA1893 flies DCA->FLL in the morning and FLL->DCA in the afternoon
    under the same number: the other leg must never match / pair.
  - UAL4520 flew as GJS4520 (DCA-ORD here) while RPA4520 flew the same
    night on another route: the codeshare translation is route-filtered.
  - shuttle numbers repeat through the day: other-time-of-day legs of the
    same direction are separate instances.
Aircraft identities are synthetic (hex 000001/000002, tails N000TS/N001TS).
"""
from datetime import datetime, timezone

import pytest

import common.db as db
from common import flight_instance as fi


def _ep(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _fdps_xml(callsign: str, hex_id: str, reg: str | None = None) -> str:
    reg_attr = f' registration="{reg}"' if reg else ""
    # a batch: an unrelated flight first, the way real MessageCollection rows look
    return ("<MessageCollection>"
            "<message><flight><flightIdentification aircraftIdentification=\"ZZZ9\"/>"
            "<aircraftDescription aircraftAddress=\"0000FF\"/></flight></message>"
            f"<message><flight><flightIdentification aircraftIdentification=\"{callsign}\"/>"
            f"<aircraftDescription aircraftAddress=\"{hex_id}\"{reg_attr}/></flight></message>"
            "</MessageCollection>")


@pytest.fixture
def fdb():
    db.init_db_all()
    fi.clear_alias_cache()
    yield db
    fi.clear_alias_cache()


def _flight_event(gufi, airline, num, origin, dest, raw="<x/>", status="active", arrival=None):
    db.upsert_flight_event(gufi, airline, num, origin, dest, None, None, arrival, status,
                           None, None, None, None, raw)


# ── airports / keys ──────────────────────────────────────────────────────────

def test_airports_agree_is_tristate():
    assert fi.airports_agree("KDCA", "DCA") is True
    assert fi.airports_agree("dca", " kdca ") is True
    assert fi.airports_agree("DCA", "FLL") is False
    assert fi.airports_agree("KDCA", "KFLL") is False
    assert fi.airports_agree("CYYC", "YYC") is True
    assert fi.airports_agree("EGLL", "LHR") is None      # different code systems: unknown, not a mismatch
    assert fi.airports_agree(None, "DCA") is None


def test_key_normalizes_carrier_airports_and_date():
    k = fi.FlightInstanceKey.make("AA1893", "KDCA", "KFLL", "2026-10-11T12:30:00Z")
    assert (k.carrier, k.flight_num, k.origin, k.destination, k.sched_date) == \
        ("AAL", "1893", "DCA", "FLL", "2026-10-11")
    assert str(k) == "AAL1893 DCA-FLL 2026-10-11"
    assert fi.FlightInstanceKey.make("N000TS") is None    # a tail is not a flight
    # the date is the UTC date of the scheduled departure (late-evening ET is next UTC day)
    assert fi.FlightInstanceKey.make("AAL1893", "DCA", "FLL", "2026-10-11T23:30:00-04:00").sched_date \
        == "2026-10-12"


def test_same_instance_rejects_other_direction_date_and_unknowns():
    morning = fi.FlightInstanceKey.make("AAL1893", "DCA", "FLL", "2026-10-11")
    assert fi.same_instance(morning, fi.FlightInstanceKey.make("AA1893", "KDCA", "KFLL", "2026-10-11"))
    assert not fi.same_instance(morning, fi.FlightInstanceKey.make("AAL1893", "FLL", "DCA", "2026-10-11"))
    assert not fi.same_instance(morning, fi.FlightInstanceKey.make("AAL1893", "DCA", "FLL", "2026-10-12"))
    assert not fi.same_instance(morning, fi.FlightInstanceKey.make("AAL1893", "DCA", "FLL", None))
    assert not fi.same_instance(morning, fi.FlightInstanceKey.make("AAL1894", "DCA", "FLL", "2026-10-11"))


# ── codeshare + resolve ──────────────────────────────────────────────────────

def _seed_ual4520(fdb):
    fdb.upsert_codeshare_mapping("UAL", "4520", "GJS", "4520", "KDCA", "KORD", "test")
    fdb.upsert_codeshare_mapping("UAL", "4520", "RPA", "4520", "KEWR", "KDCA", "test")


def test_operating_identities_are_route_filtered(fdb):
    _seed_ual4520(fdb)
    assert fi.operating_identities("UA4520", "DCA", "ORD") == [("UAL", "4520"), ("GJS", "4520")]
    assert fi.operating_identities("UAL4520", "EWR", "DCA") == [("UAL", "4520"), ("RPA", "4520")]
    # no route given: both, nothing contradicts either
    assert set(fi.operating_identities("UAL4520")) == {("UAL", "4520"), ("GJS", "4520"), ("RPA", "4520")}


def test_resolve_merges_tfms_and_fdps_and_drops_the_other_route(fdb):
    _seed_ual4520(fdb)
    fdb.upsert_flight_ooooi("G-1", callsign="GJS4520", origin="KDCA", destination="KORD",
                            original_departure="2026-10-11T22:00:00Z",
                            original_arrival="2026-10-12T00:10:00Z")
    _flight_event("G-1", "GJS", "4520", "KDCA", "KORD", _fdps_xml("GJS4520", "000001", "N000TS"))
    _flight_event("R-1", "RPA", "4520", "KEWR", "KDCA", _fdps_xml("RPA4520", "000002"))

    got = fi.resolve("UAL4520", "DCA", "ORD", "2026-10-11")
    assert len(got) == 1
    inst = got[0]
    assert inst.key.callsign == "GJS4520" and inst.marketing == "UAL4520"
    assert inst.hex == "000001" and inst.registration == "N000TS"   # from ITS OWN <flight>, not the batch's first
    assert inst.sched_dep == _ep("2026-10-11T22:00:00Z")
    assert set(inst.sources) == {"tfms", "fdps"}

    assert fi.resolve("UAL4520", "DCA", "ORD", "2026-10-12") == []     # wrong date
    assert fi.resolve("UAL4520", "DCA", "ORD", hex_id="000009") == []  # hex contradicts


def test_instances_for_hex_reparses_each_batch_row(fdb):
    _flight_event("G-1", "GJS", "4520", "KDCA", "KORD", _fdps_xml("GJS4520", "000001"))
    # the hex appears in this row's raw batch, but on ANOTHER flight's element
    _flight_event("Z-1", "ZZZ", "1", "KBOS", "KDCA",
                  "<MessageCollection><message><flight><flightIdentification aircraftIdentification=\"ZZZ1\"/>"
                  "<aircraftDescription aircraftAddress=\"000003\"/></flight></message>"
                  + _fdps_xml("ZZZ2", "000001")[len("<MessageCollection>"):])
    got = fi.instances_for_hex("000001")
    assert [i.gufi for i in got] == ["G-1"]
    assert fi.instances_for_hex("not-a-hex") == []


# ── watch matcher ────────────────────────────────────────────────────────────

def _watch(ident="AAL1893", origin="DCA", dest="FLL", sched="2026-10-11T12:00:00Z", **kw):
    e = {"id": f"wl-{ident.lower()}", "identifier": ident, "origin": origin,
         "destination": dest, "scheduled_departure": sched, "hex_id": None}
    e.update(kw)
    return e


def test_shuttle_return_leg_never_matches_the_morning_watch(fdb):
    w = _watch()
    assert fi.match_watch_entry([w], "AAL1893", origin="KDCA", destination="KFLL") is w
    assert fi.match_watch_entry([w], "AAL1893", origin="KFLL", destination="KDCA") is None
    assert fi.leg_rejection(w, "FLL", "DCA") == "cross_direction"
    # identifier-only messages (no airports) still match, as before
    assert fi.match_watch_entry([w], "aal1893") is w


def test_same_direction_other_time_of_day_is_rejected(fdb):
    w = _watch(origin="DCA", dest="LGA", sched="2026-10-11T12:00:00Z")
    assert fi.match_watch_entry([w], "AAL1893", origin="DCA", destination="LGA",
                                sched_dep="2026-10-11T12:05:00Z") is w
    assert fi.match_watch_entry([w], "AAL1893", origin="DCA", destination="LGA",
                                sched_dep="2026-10-11T19:00:00Z") is None
    assert fi.leg_rejection(w, "DCA", "LGA", "2026-10-11T19:00:00Z") == "other_time_of_day"


def test_diversion_of_the_same_leg_still_matches_but_unknown_time_does_not(fdb):
    w = _watch(origin="DCA", dest="FLL", sched="2026-10-11T12:00:00Z")
    # same origin + same scheduled departure, destination amended -> diverted leg
    assert fi.match_watch_entry([w], "AAL1893", origin="DCA", destination="MCO",
                                sched_dep="2026-10-11T12:00:00Z") is w
    # without the schedule to prove it is this leg, a different destination is another leg
    assert fi.match_watch_entry([w], "AAL1893", origin="DCA", destination="MCO") is None


def test_codeshare_operating_callsign_matches_only_on_its_route(fdb):
    _seed_ual4520(fdb)
    w = _watch("UAL4520", "DCA", "ORD", sched=None)
    assert fi.match_watch_entry([w], "GJS4520", origin="KDCA", destination="KORD") is w
    # RPA4520 is not in this watch's route-filtered aliases at all
    assert fi.match_watch_entry([w], "RPA4520", origin="KEWR", destination="KDCA") is None
    assert fi.match_watch_entry([w], "RPA4520") is None
    # GJS4520 on a different route is still another leg
    assert fi.match_watch_entry([w], "GJS4520", origin="ORD", destination="DCA") is None


def test_hex_matches_and_iata_identifier_normalizes(fdb):
    w = _watch("AA1893", hex_id="000001")
    assert fi.match_watch_entry([w], "AAL1893", origin="DCA", destination="FLL") is w
    assert fi.match_watch_entry([w], None, hex_id="000001") is w
    assert fi.match_watch_entry([w], "ZZZ1", hex_id="000002") is None


# ── time-fidelity pairing ────────────────────────────────────────────────────

def test_pair_events_stays_inside_one_instance():
    ref = [
        {"identifier": "AAL1893", "origin": "DCA", "destination": "FLL",
         "sched_dep": "2026-10-11T12:00:00Z", "phase": "off", "t": 1},
        {"identifier": "AAL1893", "origin": "FLL", "destination": "DCA",
         "sched_dep": "2026-10-11T18:00:00Z", "phase": "off", "t": 2},
    ]
    cands = [
        {"callsign": "AAL1893", "origin": "KFLL", "destination": "KDCA",
         "sched_dep": "2026-10-11T18:00:00Z", "phase": "off", "t": "b-pm"},
        {"callsign": "AAL1893", "origin": "KDCA", "destination": "KFLL",
         "sched_dep": "2026-10-11T12:00:00Z", "phase": "on", "t": "b-am-on"},
    ]
    res = fi.pair_events(ref, cands)
    assert [(a["t"], b["t"]) for a, b in res.pairs] == [(2, "b-pm")]
    assert res.rejected[0][0]["t"] == 1 and res.rejected[0][2] == "different_phase"
    assert [b["t"] for b in res.unpaired_b] == ["b-am-on"]


def test_pair_rejection_reasons():
    base = {"identifier": "AAL1893", "origin": "DCA", "destination": "FLL",
            "sched_dep": "2026-10-11T12:00:00Z"}
    assert fi.pair_rejection(base, dict(base)) is None
    assert fi.pair_rejection(base, dict(base, origin="FLL", destination="DCA")) == "cross_direction"
    assert fi.pair_rejection(base, dict(base, sched_dep="2026-10-11T17:00:00Z")) == "other_time_of_day"
    assert fi.pair_rejection(base, dict(base, sched_dep="2026-10-12T12:00:00Z")) == "different_date"
    assert fi.pair_rejection(base, dict(base, sched_dep=None)) == "no_sched"
    assert fi.pair_rejection(base, dict(base, identifier="AAL1894")) == "different_flight"
    assert fi.pair_rejection(base, dict(base, destination="MCO")) == "different_route"


# ── tail-change guard ────────────────────────────────────────────────────────

def _inst(ident, o, d, dep, arr=None, **kw):
    return fi.FlightInstance(key=fi.FlightInstanceKey.make(ident, o, d, dep),
                             sched_dep=_ep(dep), sched_arr=_ep(arr) if arr else None, **kw)


WATCHED = _inst("GJS4520", "DCA", "ORD", "2026-10-11T22:00:00Z", "2026-10-12T00:10:00Z",
                gufi="G-1")


def test_guard_accepts_authoritative_free_tail_with_a_timely_inbound():
    prev = _inst("GJS4511", "ORD", "DCA", "2026-10-11T18:30:00Z", "2026-10-11T20:40:00Z")
    d = fi.decide_tail_change(watched=WATCHED, candidate_hex="000002", candidate_registration="N001TS",
                              prior_hex="000001", sources=["fdps"],
                              candidate_instances=[prev, WATCHED], previous=prev, min_turn_min=35)
    assert d.accepted and d.changed
    assert "45 min spare over a 35-min turn" in d.reason
    assert d.note("UAL4520").startswith("UAL4520: tail change accepted 000001 -> N001TS")


def test_guard_rejects_a_tracker_alone_even_when_everything_else_fits():
    prev = _inst("GJS4511", "ORD", "DCA", "2026-10-11T18:30:00Z", "2026-10-11T20:40:00Z")
    d = fi.decide_tail_change(watched=WATCHED, candidate_hex="000002", prior_hex="000001",
                              sources=["airplanes_live"], previous=prev)
    assert not d.accepted and "not authoritative" in d.reason
    assert "NOT accepted" in d.note("UAL4520")
    # a tracker corroborating an authoritative source is fine
    assert fi.decide_tail_change(watched=WATCHED, candidate_hex="000002", prior_hex="000001",
                                 sources=["airplanes_live", "fids_destination"],
                                 previous=prev, min_turn_min=35).accepted


def test_guard_rejects_a_tail_filed_on_another_overlapping_instance():
    prev = _inst("GJS4511", "ORD", "DCA", "2026-10-11T18:30:00Z", "2026-10-11T20:40:00Z")
    other = _inst("RPA4520", "EWR", "DCA", "2026-10-11T21:30:00Z", "2026-10-11T22:45:00Z")
    d = fi.decide_tail_change(watched=WATCHED, candidate_hex="000002", prior_hex="000001",
                              sources=["fdps"], candidate_instances=[prev, other], previous=prev)
    assert not d.accepted and "filed on another instance RPA4520 EWR-DCA" in d.reason
    # the tail's NEXT leg after the watched arrival is not a conflict
    nxt = _inst("GJS4600", "ORD", "MSP", "2026-10-12T01:00:00Z", "2026-10-12T02:30:00Z")
    assert fi.decide_tail_change(watched=WATCHED, candidate_hex="000002", prior_hex="000001",
                                 sources=["fdps"], candidate_instances=[prev, nxt],
                                 previous=prev, min_turn_min=35).accepted


def test_guard_previous_leg_rules():
    common = dict(watched=WATCHED, candidate_hex="000002", prior_hex="000001",
                  sources=["fdps"], min_turn_min=35)
    late = _inst("GJS4511", "ORD", "DCA", "2026-10-11T19:30:00Z", "2026-10-11T21:40:00Z")
    d = fi.decide_tail_change(previous=late, **common)
    assert not d.accepted and "15 min short of a 35-min turn" in d.reason

    elsewhere = _inst("GJS4511", "ORD", "BWI", "2026-10-11T18:30:00Z", "2026-10-11T20:40:00Z")
    assert "ends at BWI, not DCA" in fi.decide_tail_change(previous=elsewhere, **common).reason

    assert "previous leg of the tail unknown" in fi.decide_tail_change(previous=None, **common).reason
    assert fi.decide_tail_change(previous=None, on_ground_at_origin=True, **common).accepted

    cx = _inst("GJS4511", "ORD", "DCA", "2026-10-11T18:30:00Z", "2026-10-11T20:40:00Z",
               status="cancelled")
    assert "cancelled" in fi.decide_tail_change(previous=cx, **common).reason

    same = fi.decide_tail_change(watched=WATCHED, candidate_hex="000001", prior_hex="000001",
                                 sources=["fdps"])
    assert not same.accepted and not same.changed


def test_previous_leg_picks_the_latest_arrival_into_the_origin():
    early = _inst("GJS4501", "ORD", "DCA", "2026-10-11T12:00:00Z", "2026-10-11T14:00:00Z")
    later = _inst("GJS4511", "ORD", "DCA", "2026-10-11T18:30:00Z", "2026-10-11T20:40:00Z")
    away = _inst("GJS4600", "ORD", "MSP", "2026-10-12T01:00:00Z", "2026-10-12T02:30:00Z")
    assert fi.previous_leg([early, away, later, WATCHED], WATCHED) is later


# ── inbound tracker ──────────────────────────────────────────────────────────

def test_inbound_on_time_late_diverted_cancelled_unknown():
    ok = _inst("GJS4511", "ORD", "DCA", "2026-10-11T18:30:00Z", "2026-10-11T20:40:00Z")
    a = fi.assess_inbound(WATCHED, ok, min_turn_min=35)
    assert a.status == "on_time" and a.slack_min == 45 and a.flags == ()
    assert a.propagated_departure == _ep("2026-10-11T22:00:00Z")

    late = _inst("GJS4511", "ORD", "DCA", "2026-10-11T18:30:00Z", "2026-10-11T20:40:00Z",
                 est_arr=_ep("2026-10-11T21:50:00Z"))
    a = fi.assess_inbound(WATCHED, late, min_turn_min=35)
    assert a.status == "late_beyond_turn" and "inbound_late_beyond_turn_buffer" in a.flags
    assert a.propagated_departure == _ep("2026-10-11T22:25:00Z") and a.slack_min == -25
    assert "25 min late" in a.summary

    landed = _inst("GJS4511", "ORD", "DCA", "2026-10-11T18:30:00Z", "2026-10-11T20:40:00Z",
                   act_arr=_ep("2026-10-11T20:30:00Z"))
    assert fi.assess_inbound(WATCHED, landed, min_turn_min=35).status == "at_origin"

    div = _inst("GJS4511", "ORD", "BWI", "2026-10-11T18:30:00Z", "2026-10-11T20:40:00Z")
    a = fi.assess_inbound(WATCHED, div)
    assert a.status == "diverted" and a.propagated_departure is None

    cx = _inst("GJS4511", "ORD", "DCA", "2026-10-11T18:30:00Z", "2026-10-11T20:40:00Z",
               status="cancelled")
    assert fi.assess_inbound(WATCHED, cx).flags == ("inbound_cancelled",)
    assert fi.assess_inbound(WATCHED, None).status == "unknown"


def test_min_turn_default_and_env_override(monkeypatch):
    monkeypatch.delenv("CTDC_MIN_TURN_MIN_NARROWBODY", raising=False)
    assert fi.min_turn_minutes() == 35
    monkeypatch.setenv("CTDC_MIN_TURN_MIN_NARROWBODY", "50")
    assert fi.min_turn_minutes("narrowbody") == 50
    monkeypatch.setenv("CTDC_MIN_TURN_MIN_NARROWBODY", "5000")
    assert fi.min_turn_minutes("narrowbody") == 240
    assert fi.min_turn_minutes("widebody") == 75
