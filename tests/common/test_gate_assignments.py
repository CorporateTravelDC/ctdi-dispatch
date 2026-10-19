"""2026-10-18 (operator, gate batch): gate / terminal / baggage assignment model,
resolver, ACARS binding, TDES evidence and the change-only push.

Isolated SQLite only (tests/conftest.py); every flight here is synthetic (flight
numbers 09xx, no real tail, hex or registration). The watchlist push is captured,
never sent."""
from __future__ import annotations

import calendar
import time

import pytest

import common.db as db
from common import flight_instance as fi
from common import gate_assignments as ga


def ep(iso: str) -> float:
    return float(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")))


NOW = ep("2026-10-17T15:00:00Z")


@pytest.fixture
def gdb(monkeypatch):
    db.init_db_all()
    fi.clear_alias_cache()
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda ids: set())
    monkeypatch.setattr(db, "faa_ladd_hex_batch", lambda hx: set())
    yield
    fi.clear_alias_cache()


@pytest.fixture
def hits(monkeypatch):
    import shared.watchlist as wl
    out = []
    monkeypatch.setattr(wl, "watchlist_event_hit",
                        lambda eid, summary, detail, priority=3: out.append((eid, summary, detail)))
    return out


def tfms(gufi, callsign, origin, dest, dep_iso, arr_iso, **kw):
    with db.conn() as c:
        c.execute("INSERT INTO flight_ooooi_times (gufi, callsign, airline, flight_num, origin, destination, "
                  "original_departure, original_arrival, airline_off_time, airline_on_time, updated_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (gufi, callsign, callsign[:3], callsign[3:], origin, dest, dep_iso, arr_iso,
                   kw.get("off"), kw.get("on"), NOW))


KEY = ga.make_key("UAL", "999", "ORD", "IAD", "2026-10-17")


def obs(source, value, *, field="gate", side="arrival", observed="2026-10-17T14:00:00Z", match="exact",
        evidence=None, conf=None, key=KEY, airport="IAD", event_key=None):
    evidence = evidence or {"swim_tdes": "clearance", "aeroapi": "api", "mwaa_fids": "display"}.get(
        source, "assignment_msg")
    conf = conf if conf is not None else {"mwaa_fids": 0.7, "aeroapi": 0.8}.get(source, 0.9)
    if source == "mwaa_fids":
        match = "route_watch" if match == "exact" else match
    return ga.Observation(key=key, side=side, field=field, value_raw=value, source=source,
                          observed_at=observed, leg_match=match, evidence_type=evidence, confidence=conf,
                          parser_version="test", airport=airport, event_key=event_key)


def view(key=KEY, now=NOW):
    return ga.leg_assignments(key, now)


# ── resolver: precedence, placeholders, conflicts, ordering ───────────────────

def test_fids_absent_no_enrichment_at_all_reads_unknown(gdb):
    v = view()
    assert {k: f["status"] for k, f in v.items()} == {
        "arrival_gate": "unknown", "arrival_terminal": "unknown",
        "arrival_baggage": "unknown", "departure_gate": "unknown"}
    assert all(f["value"] is None for f in v.values())


KEYD = ga.make_key("UAL", "995", "DCA", "ORD", "2026-10-17")


def test_precedence_swim_over_acars_over_aeroapi_over_fids(gdb):
    dep = lambda src, val, **k: obs(src, val, side="departure", airport="DCA", key=KEYD, **k)  # noqa: E731
    ga.record([dep("mwaa_fids", "F1")], now=NOW)
    assert view(KEYD)["departure_gate"]["value"] == "F1"
    ga.record([dep("aeroapi", "F2")], now=NOW)
    assert view(KEYD)["departure_gate"]["value"] == "F2"
    ga.record([dep("acars_airframes", "F3")], now=NOW)
    assert view(KEYD)["departure_gate"]["value"] == "F3"
    ga.record([dep("swim_tdes", "F4")], now=NOW)
    f = view(KEYD)["departure_gate"]
    assert (f["value"], f["status"], f["source"]) == ("F4", "observed", "SWIM TDES")
    # a lower tier arriving later never replaces the live higher-tier value
    ga.record([dep("mwaa_fids", "F9")], now=NOW + 60)
    ga.record([dep("acars_jumpseat", "F8", observed="2026-10-17T14:30:00Z")], now=NOW + 60)
    assert view(KEYD, now=NOW + 60)["departure_gate"]["value"] == "F4"


def test_placeholders_never_count_and_never_block_the_fallback(gdb):
    for raw in ("G", "N/A", "", None, "XXXX"):
        ga.record([obs("swim_tdes", raw, side="departure", airport="DCA", key=KEYD,
                       event_key=f"ph-{raw}")], now=NOW)
    assert view(KEYD)["departure_gate"]["status"] == "unknown"
    ga.record([obs("mwaa_fids", "C9", side="departure", airport="DCA", key=KEYD)], now=NOW)
    assert view(KEYD)["departure_gate"]["value"] == "C9"    # FIDS fills in: the placeholders were nothing


def test_fids_observations_are_dc_airports_only(gdb):
    with pytest.raises(ValueError):
        ga.record([obs("mwaa_fids", "C9", side="departure", airport="ORD")], now=NOW)


def test_g_prefixed_tdes_gate_kept_raw_and_flagged_provisional(gdb):
    ga.record([obs("swim_tdes", "GC33", side="departure")], now=NOW)
    f = view()["departure_gate"]
    assert (f["value"], f["status"]) == ("GC33", "provisional")   # not normalised to C33 (unproven)
    with db.conn() as c:
        row = dict(c.execute("SELECT flags FROM gate_baggage_observations").fetchone())
    assert row["flags"] == "g_prefix_unverified"


def test_same_tier_disagreement_is_conflicting_and_not_pushed(gdb, hits):
    ga.record([obs("acars_airframes", "C9", observed="2026-10-17T14:00:00Z")], now=NOW)
    ga.record([obs("acars_jumpseat", "D30", observed="2026-10-17T14:05:00Z")], now=NOW)
    f = view()["arrival_gate"]
    assert f["status"] == "conflicting" and f["value"] is None
    assert f["conflicting_values"] == ["C9", "D30"]
    entry = {"id": "wl-g1", "identifier": "UAL999"}
    assert ga.push_watch_changes(entry, "UAL999", KEY, now=NOW) == 0
    assert hits == []


def test_newer_gate_change_supersedes_across_same_tier_sources(gdb):
    ga.record([obs("acars_airframes", "C9", observed="2026-10-17T14:00:00Z")], now=NOW)
    ga.record([obs("acars_jumpseat", "D30", observed="2026-10-17T14:20:00Z",
                   evidence="assignment_change")], now=NOW)
    f = view()["arrival_gate"]
    assert (f["value"], f["status"]) == ("D30", "observed")
    assert {"value": "C9", "status": "superseded"}.items() <= f["superseded"][0].items()


def test_delayed_out_of_order_message_does_not_win(gdb):
    ga.record([obs("acars_airframes", "D30", observed="2026-10-17T14:20:00Z",
                   evidence="assignment_change")], now=NOW)
    # the OLDER assignment arrives late (feeder backlog): ordered by the message's own time
    ga.record([obs("acars_airframes", "C9", observed="2026-10-17T14:00:00Z")], now=NOW + 120)
    assert view(now=NOW + 120)["arrival_gate"]["value"] == "D30"


def test_duplicate_replay_stores_once_and_writes_no_new_revision(gdb):
    o = obs("acars_airframes", "C9", event_key="UA_GATE_ASSIGN|UA|999|17|KIAD|C9|14:23:53")
    r1 = ga.record([o], now=NOW)
    r2 = ga.record([o], now=NOW + 30)
    assert (r1.stored, r2.stored, r2.duplicates) == (1, 0, 1)
    assert len(r1.changes) == 1 and r2.changes == []
    with db.conn() as c:
        n = dict(c.execute("SELECT count(*) AS n FROM gate_baggage_assignment_history").fetchone())["n"]
    assert n == 1


def test_fids_value_expires_past_the_staleness_cap(gdb):
    ga.record([obs("mwaa_fids", "B12", field="baggage")], now=NOW)
    assert view(now=NOW + 60)["arrival_baggage"]["value"] == "B12"
    stale = view(now=NOW + ga.fids_max_stale_s() + 1)["arrival_baggage"]
    assert stale["status"] == "unknown" and stale["value"] is None   # never an old carousel


def test_fids_blank_after_a_value_means_no_value_now(gdb):
    ga.record([obs("mwaa_fids", "B12", field="baggage")], now=NOW)
    ga.record([obs("mwaa_fids", None, field="baggage")], now=NOW + 120)
    assert view(now=NOW + 130)["arrival_baggage"]["status"] == "unknown"


def test_independent_baggage_only_source_beside_a_swim_gate(gdb):
    ga.record([obs("swim_tdes", "C9", side="departure", airport="ORD")], now=NOW)
    ga.record([obs("mwaa_fids", "7", field="baggage")], now=NOW)
    v = view()
    assert v["departure_gate"]["value"] == "C9" and v["departure_gate"]["source_id"] == "swim_tdes"
    assert v["arrival_baggage"]["value"] == "7" and v["arrival_baggage"]["source_id"] == "mwaa_fids"
    assert v["arrival_gate"]["status"] == "unknown"


def test_no_observation_field_can_carry_a_time_status_or_tail():
    fields = set(ga.Observation.__dataclass_fields__)
    assert not fields & {"status", "scheduled", "estimated", "remaining", "tail", "registration", "hex"}


# ── LADD ──────────────────────────────────────────────────────────────────────

def test_ladd_listed_leg_is_dropped_before_storage(gdb, monkeypatch):
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda ids: {i for i in ids if i == "UAL999"})
    r = ga.record([obs("swim_tdes", "C9", side="departure")], now=NOW)
    assert r.ladd_dropped and r.stored == 0
    with db.conn() as c:
        assert dict(c.execute("SELECT count(*) AS n FROM gate_baggage_observations").fetchone())["n"] == 0


def test_ladd_lookup_failure_fails_closed(gdb, monkeypatch):
    def boom(ids):
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "faa_ladd_listed_batch", boom)
    r = ga.record([obs("mwaa_fids", "C9")], now=NOW)
    assert r.ladd_dropped and r.stored == 0


# ── pushes ────────────────────────────────────────────────────────────────────

def test_push_on_change_only_and_never_for_unknown(gdb, hits):
    entry = {"id": "wl-g2", "identifier": "UAL999"}
    assert ga.push_watch_changes(entry, "UAL999", KEY, now=NOW) == 0          # unknown: nothing
    ga.record([obs("mwaa_fids", "C9")], now=NOW)
    assert ga.push_watch_changes(entry, "UAL999", KEY, now=NOW) == 1
    assert ga.push_watch_changes(entry, "UAL999", KEY, now=NOW + 60) == 0     # unchanged
    eid, summary, detail = hits[0]
    assert detail["watchlist_trigger"] == "gate_assignment" and detail["slot"] == "arrival:gate"
    assert "C9" in summary and "provisional" in summary
    for word in ("Landed", "InGate", "InAir", "Scheduled", "Delayed", "OutGate"):
        assert word not in summary
    # baggage on the same leg gets its own slot
    ga.record([obs("mwaa_fids", "4", field="baggage")], now=NOW + 60)
    assert ga.push_watch_changes(entry, "UAL999", KEY, now=NOW + 60) == 1
    assert hits[-1][2]["slot"] == "arrival:baggage"


def test_same_value_from_a_new_source_with_same_status_does_not_push(gdb, hits):
    entry = {"id": "wl-g3", "identifier": "UAL999"}
    ga.record([obs("acars_airframes", "C9", observed="2026-10-17T14:00:00Z")], now=NOW)
    assert ga.push_watch_changes(entry, "UAL999", KEY, now=NOW) == 1
    ga.record([obs("acars_jumpseat", "C9", observed="2026-10-17T14:01:00Z")], now=NOW + 30)
    assert ga.push_watch_changes(entry, "UAL999", KEY, now=NOW + 30) == 0
    assert view(now=NOW + 30)["arrival_gate"]["verification"] == "cross_source_agree"


def test_push_key_is_content_only():
    a = ga.push_key("L", "arrival", "gate", "C9", "observed")
    assert a == ga.push_key("L", "arrival", "gate", "C9", "observed")
    assert a != ga.push_key("L", "arrival", "gate", "C9", "provisional")
    assert a != ga.push_key("L", "arrival", "baggage", "C9", "observed")


# ── ACARS binding ─────────────────────────────────────────────────────────────

UA_ASSIGN = ("QUNDCULUA~1GATE ASSIGN UA0999/17 KORD KIAD SENT: 14:23:53Z ***ACCOMPLISH TERMINATION CHECKLIST*** "
             "GATE C9 AVAIL 1544 RAMP FREQ 129.075 OPS FREQ 130.500")


def env(text, ts="2026-10-17T14:23:55Z", source="AIRFRAMES", direction="up", flight="", label="RA"):
    return {"source": source, "text": text, "timestamp": ts, "direction": direction, "flight": flight,
            "label": label, "msg_id": "m1"}


def ua_leg():
    tfms("G-UA999", "UAL999", "KORD", "KIAD", "2026-10-17T12:30:00Z", "2026-10-17T15:10:00Z",
         off="2026-10-17T12:50:00Z")


def test_acars_gate_only_confirmed_and_observed(gdb):
    ua_leg()
    counts = ga.ingest_acars([env(UA_ASSIGN)])
    assert counts["stored"] == 1, counts
    f = view(ga.make_key("UAL", "999", "ORD", "IAD", "2026-10-17"), now=NOW)["arrival_gate"]
    assert (f["value"], f["status"], f["source"]) == ("C9", "observed", "ACARS")


def test_acars_wrong_date_wrong_leg_and_ambiguous_are_dropped(gdb):
    ua_leg()
    wrong_day = UA_ASSIGN.replace("UA0999/17", "UA0999/03")
    assert ga.ingest_acars([env(wrong_day)]).get("drop_wrong_date") == 1
    wrong_dest = UA_ASSIGN.replace("KORD KIAD", "KORD KDCA")
    assert ga.ingest_acars([env(wrong_dest)]).get("drop_no_leg") == 1
    # outside the leg's window (yesterday's message): no leg
    assert ga.ingest_acars([env(UA_ASSIGN, ts="2026-10-16T09:00:00Z")]).get("drop_no_leg") == 1
    # a second instance of the same number in the same window (shuttle): never a guess
    tfms("G-UA999b", "UAL999", "KORD", "KIAD", "2026-10-17T13:30:00Z", "2026-10-17T16:10:00Z")
    assert ga.ingest_acars([env(UA_ASSIGN)]).get("drop_ambiguous_leg") == 1
    with db.conn() as c:
        assert dict(c.execute("SELECT count(*) AS n FROM gate_baggage_observations").fetchone())["n"] == 0


def test_acars_wrong_aircraft_envelope_flight_binds_to_its_own_leg_not_ours(gdb):
    ua_leg()
    tfms("G-UA998", "UAL998", "KDEN", "KIAD", "2026-10-17T11:00:00Z", "2026-10-17T15:00:00Z")
    # a header-less uplink names no flight: the envelope's flight id decides
    ga.ingest_acars([env("GATE ASSIGN GATE E6 AVAIL 1405 RAMP FREQ 131.000", flight="UA998")])
    ours = view(ga.make_key("UAL", "999", "ORD", "IAD", "2026-10-17"))["arrival_gate"]
    theirs = view(ga.make_key("UAL", "998", "DEN", "IAD", "2026-10-17"))["arrival_gate"]
    assert ours["status"] == "unknown"
    assert (theirs["value"], theirs["status"]) == ("E6", "provisional")    # probable format


def test_acars_downlink_or_unproven_format_is_never_confirmed(gdb):
    ua_leg()
    ga.ingest_acars([env(UA_ASSIGN, direction="down")])
    f = view(ga.make_key("UAL", "999", "ORD", "IAD", "2026-10-17"))["arrival_gate"]
    assert f["status"] == "provisional"


def test_acars_requests_and_psa_acks_are_signals_only(gdb):
    ua_leg()
    c = ga.ingest_acars([env("/C3 GATE REQ       / KORD KIAD 17 ABC123 0999 ---- ----", direction="down"),
                         env("/ACKFLT GATE E5", direction="down", flight="UA999")])
    assert c["signals"] == 2 and c["stored"] == 0


def test_acars_noise_never_produces_a_gate(gdb):
    ua_leg()
    c = ga.ingest_acars([env("TOWER FREQ 118.5. ADZ GATE ASSIGNMENT TO APPROACH CTL ON INITIAL CTC."),
                         env("PLZ HAVE MED PERSONNEL MEET US AT GATE", flight="UA999")])
    assert c["stored"] == 0 and c["parsed"] == 0


def test_acars_jetblue_carousel_counts_as_baggage(gdb):
    tfms("G-B6", "JBU901", "KFLL", "KBWI", "2026-10-17T12:00:00Z", "2026-10-17T14:40:00Z")
    c = ga.ingest_acars([env("GATE:C23B CAROUSEL:8 AC TURN: FLL ON TIME 1555L", flight="B6901",
                             ts="2026-10-17T14:00:00Z", label="41")])
    assert c["stored"] == 2, c
    v = view(ga.make_key("JBU", "901", "FLL", "BWI", "2026-10-17"))
    assert v["arrival_baggage"]["value"] == "8" and v["arrival_gate"]["value"] == "C23B"


def test_acars_out_of_scope_leg_is_not_stored(gdb):
    tfms("G-UA997", "UAL997", "KORD", "KDEN", "2026-10-17T12:30:00Z", "2026-10-17T15:10:00Z")
    text = UA_ASSIGN.replace("UA0999/17 KORD KIAD", "UA0997/17 KORD KDEN")
    assert ga.ingest_acars([env(text)]).get("out_of_scope") == 1


def test_forwarded_hit_is_revalidated(gdb):
    ua_leg()
    good = {"source": "JUMPSEAT", "timestamp": "2026-10-17T14:23:55Z", "direction": "Ground to Air",
            "flight": "", "label": "", "hit": {"fmt": "UA_GATE_ASSIGN", "kind": "ARR_GATE", "al": "UA",
                                               "flt": "0999", "day": "17", "org": "KORD", "dst": "KIAD",
                                               "sent": "14:23:53", "gate": "C9", "chg": "ASSIGN"}}
    bad = dict(good, hit={"fmt": "NOT_A_FORMAT", "kind": "ARR_GATE", "gate": "C9"})
    evil = dict(good, hit=dict(good["hit"], gate="C9<script>"))
    c = ga.ingest_acars([good, bad, evil])
    assert c["stored"] == 1 and c["bad_hit"] == 2


def test_acars_ladd_listed_bound_leg_is_dropped(gdb, monkeypatch):
    ua_leg()
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda ids: {i for i in ids if i == "UAL999"})
    c = ga.ingest_acars([env(UA_ASSIGN)])
    assert c["ladd_dropped"] == 1 and c["stored"] == 0


# ── TDES ──────────────────────────────────────────────────────────────────────

def _tdes(gate, et="2026-10-17T12:05:00Z"):
    return {"kind": "tdes_departure", "airport": "DCA", "callsign": "UAL996", "event_time": et,
            "parking_gate": gate, "destination_airport": "ORD"}


def test_tdes_clearance_binds_to_the_verified_leg(gdb):
    tfms("G-UA996", "UAL996", "KDCA", "KORD", "2026-10-17T12:40:00Z", "2026-10-17T14:50:00Z")
    key, r = ga.record_tdes(_tdes("C27"))
    assert str(key) == "UAL996 DCA-ORD 2026-10-17" and r.stored == 1
    f = view(key)["departure_gate"]
    assert (f["value"], f["status"], f["source"]) == ("C27", "observed", "SWIM TDES")


def test_tdes_placeholder_gate_is_stored_as_placeholder_only(gdb):
    key, _r = ga.record_tdes(_tdes("G"))
    assert view(key)["departure_gate"]["status"] == "unknown"
    key, _r = ga.record_tdes(_tdes("N/A", et="2026-10-17T12:06:00Z"))
    assert view(key)["departure_gate"]["status"] == "unknown"


# ── API ───────────────────────────────────────────────────────────────────────

def _api(ident, **q):
    import json as _json
    from auth.auth import Tier
    from web.routes import assignments
    q.setdefault("origin", None)
    q.setdefault("destination", None)
    q.setdefault("date", None)
    return _json.loads(assignments.flight_assignments(ident, tier=Tier.T1, **q).body)


def test_assignments_route_is_always_200_with_unknown_fields(gdb):
    out = _api("UA999")
    assert out["legs"] == [] and out["fields"]["arrival_gate"]["status"] == "unknown"
    assert set(out["fields"]) == {"arrival_gate", "arrival_terminal", "arrival_baggage", "departure_gate"}


def test_assignments_route_serves_resolved_fields_by_marketing_or_dated_leg(gdb, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)
    ga.record([obs("swim_tdes", "C9", side="departure", airport="ORD")], now=NOW)
    out = _api("UA999", date="2026-10-17")
    assert out["legs"][0]["leg_key"] == str(KEY)
    f = out["fields"]["departure_gate"]
    assert (f["value"], f["status"], f["source"]) == ("C9", "observed", "SWIM TDES")
    assert f["observed_at"].endswith("Z")
    assert _api("UAL999", destination="IAD")["fields"]["departure_gate"]["value"] == "C9"
    assert _api("UA999", destination="DCA")["legs"] == []      # route contradicts: not this leg


def test_assignments_route_answers_unknown_for_a_ladd_listed_flight(gdb, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)
    ga.record([obs("swim_tdes", "C9", side="departure", airport="ORD")], now=NOW)
    monkeypatch.setattr(db, "faa_ladd_listed_batch", lambda ids: {"UAL999"} & set(ids))
    out = _api("UA999", date="2026-10-17")
    assert out["legs"] == [] and out["fields"]["departure_gate"]["status"] == "unknown"


def test_assignments_route_contains_no_fids_status_time_or_tail(gdb, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)
    ga.record([obs("mwaa_fids", "C9"), obs("mwaa_fids", "7", field="baggage")], now=NOW)
    import json as _json
    blob = _json.dumps(_api("UA999"))
    for k in ('"scheduled"', '"estimated"', '"remaining"', '"tail"', '"registration"'):
        assert k not in blob


def test_retention_prune_runs(gdb):
    ga.record([obs("swim_tdes", "C9", side="departure", airport="ORD")], now=NOW)
    assert db.prune_gate_baggage(days=120) == 0
    assert db.prune_gate_baggage(days=-1) >= 1


def test_watchlist_dedup_gives_each_slot_its_own_key_and_ignores_the_source():
    import shared.watchlist as wl
    base = {"watchlist_trigger": "gate_assignment", "slot": "arrival:gate", "side": "arrival",
            "field": "gate", "value": "C9", "assignment_status": "observed", "leg_key": "L", "source": "ACARS"}
    assert wl._check_dedup("wl-d", "gate_assignment", dict(base)) is False          # first: push
    assert wl._check_dedup("wl-d", "gate_assignment", dict(base, source="MWAA FIDS")) is True   # source only
    bag = dict(base, slot="arrival:baggage", field="baggage", value="7")
    assert wl._check_dedup("wl-d", "gate_assignment", bag) is False                 # own slot
    assert wl._check_dedup("wl-d", "gate_assignment", dict(base)) is True           # gate slot untouched
