"""TFDM pre-departure source authority (2026-11-19, operator: "allow it to precede SFDPS").

Every fixture below is SYNTHETIC: made-up callsigns, registrations and hex values shaped like the
NASA ATD-2 "TTP Flight Data Messages" example (NTRS 20180007226). No real flight or aircraft.
"""
import pytest

from common import tfdm_authority as ta
from common import flight_instance as fi
from ingest.parsers import tfdm_parser as tp

SYNTH_XML = """<nas:NasMessage xmlns:nas="http://www.faa.aero/nas/4.1" xmlns:fx="http://www.fixm.aero/flight/4.0">
 <nas:flight>
  <fx:aircraft registration="NQ0001"/>
  <fx:departure departurePointText="KSAV">
   <nas:offBlockTime><nas:scheduled>2026-11-18T18:30:00.000Z</nas:scheduled></nas:offBlockTime>
   <nas:runwayDepartureTime/>
   <nas:departureDelay tmiIdentifier="12KSAV,13KSAV"/>
   <nas:approvalRequestReleaseTime>2026-11-18T18:40:00.000Z</nas:approvalRequestReleaseTime>
   <nas:standInformation standName="B3"/>
  </fx:departure>
  <fx:destination destinationPointText="KIAD"/>
  <nas:additionalFlightInformation><nas:nameValue name="TFDMID" value="SYNTH.1"/></nas:additionalFlightInformation>
  <nas:flightIdentification aircraftIdentification="ZZZ4006"/>
  <nas:flightPlan identifier="SYNTH-GUFI-1"/>
 </nas:flight>
 <nas:metadata messageType="FlightUpdate"/>
</nas:NasMessage>"""

DAY = fi.to_epoch("2026-11-18T17:00:00Z")   # 12:00 ET on the watch's date


def _entry(**kw):
    e = {"id": "wl-flight-zzz4006-20261118", "identifier": "ZZZ4006", "origin": "KSAV",
         "destination": "KIAD", "entry_type": "flight", "hex_id": None, "registration": None,
         "oooi_phase": None}
    e.update(kw)
    return e


def _rec(**kw):
    r = tp.parse_ttp_message(SYNTH_XML.encode(), {"DATA_GROUP": "FlightData"})["flights"][0]
    r.update(kw)
    return r


@pytest.fixture
def world(monkeypatch):
    hits, binds = [], []
    from common import db
    import shared.watchlist as wl
    import shared.pending_identity as pi
    monkeypatch.setattr(wl, "watchlist_event_hit", lambda eid, s, d, priority=3: hits.append((eid, s, d, priority)))
    monkeypatch.setattr(db, "set_watchlist_identity", lambda eid, hex_id=None, registration=None, source="adsb":
                        binds.append((eid, hex_id, registration, source)))
    reg = {"faa": {"NQ0001": "abc123"}, "osk": {"NQ0001": "abc123"}}
    monkeypatch.setattr(db, "faa_lookup_by_n_number",
                        lambda n: {"mode_s_hex": reg["faa"][n]} if n in reg["faa"] else None)
    monkeypatch.setattr(db, "opensky_lookup_by_registration",
                        lambda n: {"icao24": reg["osk"][n]} if n in reg["osk"] else None)
    monkeypatch.setattr(pi, "is_withheld_identity", lambda h, r: False)
    monkeypatch.setattr(ta, "_clearance_field_seen", False)
    return {"hits": hits, "binds": binds, "reg": reg}


def test_parser_reads_documented_fields():
    r = _rec()
    assert r["registration"] == "NQ0001" and r["eram_gufi"] == "SYNTH-GUFI-1"
    assert r["tmi_ids"] == ["12KSAV", "13KSAV"] and r["apreq_release"].startswith("2026-11-18T18:40")
    assert r["clearance_delivery"] is None and r["sched_off_block"].startswith("2026-11-18T18:30")


def test_tfdm_binds_before_departure_ahead_of_missing_sfdps(world):
    assert ta.bind_identity(_entry(), _rec(), DAY) == "bound"
    assert world["binds"] == [("wl-flight-zzz4006-20261118", "abc123", "NQ0001", "tfdm")]
    assert "IDENTITY CONFIRMED NQ0001/abc123" in world["hits"][0][1]
    assert "tfdm" in fi.AUTHORITATIVE_TAIL_SOURCES and "tfdm" in fi.SWIM_TAIL_SOURCES


def test_registry_disagreement_blocks(world):
    world["reg"]["osk"]["NQ0001"] = "def456"
    assert ta.bind_identity(_entry(), _rec(), DAY) == "conflicting"
    assert world["binds"] == [] and world["hits"][0][2]["state"] == "tfdm_registry_conflict"


def test_registry_unknown_does_not_bind(world):
    world["reg"]["faa"].clear()
    assert ta.bind_identity(_entry(), _rec(), DAY) == "registry_unknown" and world["binds"] == []


def test_other_day_record_ignored(world):
    r = _rec(sched_off_block="2026-11-17T18:30:00.000Z")
    assert ta.bind_identity(_entry(), r, DAY) == "other_day_occurrence"
    assert ta.alerts(_entry(), r, DAY, has_fdps_plan=lambda e: False) == []
    assert world["binds"] == [] and world["hits"] == []


def test_wrong_route_ignored(world):
    assert ta.bind_identity(_entry(), _rec(destination_airport="KORD"), DAY) == "destination_disagrees"


def test_post_departure_precedence_unchanged(world):
    assert ta.bind_identity(_entry(oooi_phase="off"), _rec(), DAY) == "airborne"
    assert ta.bind_identity(_entry(), _rec(actual_takeoff="2026-11-18T18:51:00Z"), DAY) == "airborne"
    assert ta.bind_identity(_entry(hex_id="111111"), _rec(), DAY) == "already_identified"
    assert world["binds"] == []


def test_alerts_fire_and_share_the_guard_slot(world):
    now = fi.to_epoch("2026-11-18T18:00:00Z")          # 30 min before off-block
    sent = ta.alerts(_entry(), _rec(), now, has_fdps_plan=lambda e: False)
    assert sent == ["fdps_plan_missing_predeparture", "tfdm_tmi", "tfdm_apreq_release"]
    miss = [h for h in world["hits"] if h[2]["watchlist_trigger"] == "fdps_plan_missing_predeparture"][0]
    assert miss[2]["state"] == "plan_missing_predeparture"     # same slot as common.fdps_predeparture
    assert "TFDM has the flight" in miss[1] and "Z (" in miss[1] and "ET)" in miss[1]
    assert [h for h in world["hits"] if h[2]["watchlist_trigger"] == "tfdm_tmi"][0][2]["tmi"] == "12KSAV,13KSAV"


def test_no_plan_alert_when_plan_on_file_or_far_off(world):
    far = fi.to_epoch("2026-11-18T12:00:00Z")
    assert "fdps_plan_missing_predeparture" not in ta.alerts(_entry(), _rec(), far, has_fdps_plan=lambda e: False)
    near = fi.to_epoch("2026-11-18T18:00:00Z")
    assert "fdps_plan_missing_predeparture" not in ta.alerts(_entry(), _rec(), near, has_fdps_plan=lambda e: True)


def test_awaiting_clearance_is_self_calibrating(world):
    near = fi.to_epoch("2026-11-18T18:10:00Z")
    r = _rec(tmi_ids=[], apreq_release=None)
    assert ta.alerts(_entry(), r, near, has_fdps_plan=lambda e: True) == []          # field never seen
    ta.alerts(_entry(id="other-20261118"), _rec(clearance_delivery="2026-11-18T17:50:00Z", tmi_ids=[],
                                                  apreq_release=None), near, has_fdps_plan=lambda e: True)
    assert ta.alerts(_entry(), r, near, has_fdps_plan=lambda e: True) == ["tfdm_awaiting_clearance"]


def test_kinds_registered_with_stable_identities():
    from common.push_policy_registry import KINDS
    assert KINDS[("aviation", "tfdm_awaiting_clearance")].identity == ("state",)
    assert KINDS[("aviation", "tfdm_tmi")].identity == ("tmi",)
    assert KINDS[("aviation", "tfdm_apreq_release")].identity == ("release",)
    assert KINDS[("aviation", "fdps_plan_missing_predeparture")].identity == ("state",)


def test_ladd_withheld_confirmation(world, monkeypatch):
    import shared.pending_identity as pi
    monkeypatch.setattr(pi, "is_withheld_identity", lambda h, r: True)
    assert ta.bind_identity(_entry(), _rec(), DAY) == "bound"
    s, d = world["hits"][0][1], world["hits"][0][2]
    assert "withheld" in s and "NQ0001" not in s and "hex_id" not in d and "registration" not in d


def test_process_never_raises_and_uses_watch_cache(world, monkeypatch):
    from common import db
    monkeypatch.setattr(db, "get_watchlist_entries", lambda: [_entry()])
    monkeypatch.setattr(ta, "_watch_cache", {"at": 0.0, "by_callsign": {}})
    assert ta.process(_rec(), DAY) is True
    assert ta.process(_rec(), DAY) is False                     # cached entry now carries the tail
    assert ta.process({"callsign": None}) is False
