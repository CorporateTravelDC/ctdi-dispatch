"""
2026-10-13 -- TFMS/TBFM watch matchers are instance-aware
(common.flight_instance.match_watch_entry; docs/FLIGHT_INSTANCE_RESOLVER.md).

Before: both parsers matched on the callsign alone, so AA1893's FLL->DCA
afternoon leg advanced a DCA->FLL morning watch, and a regional's own
callsign (GJS4520) never matched a watch filed under the brand number.
"""
import xml.etree.ElementTree as ET

import pytest

import common.db as db
from common import flight_instance as fi


MORNING = {"id": "wl-flight-aal1893-test", "identifier": "AAL1893", "origin": "KDCA",
           "destination": "KFLL", "scheduled_departure": "2026-10-11T12:00:00Z", "hex_id": None}


@pytest.fixture
def watches(monkeypatch):
    db.init_db_all()
    fi.clear_alias_cache()
    entries = [dict(MORNING)]
    import shared.watchlist as wl
    monkeypatch.setattr(wl, "get_active_entries", lambda entry_type=None: entries)
    yield entries
    fi.clear_alias_cache()


def _fltd(msg_type, callsign, dep, arr, igtd=None, body="ncsmFlightCreate"):
    igtd_xml = f"<igtd>{igtd}</igtd>" if igtd else ""
    return ET.fromstring(
        f'<fltdMessage msgType="{msg_type}" acid="{callsign}" depArpt="{dep}" arrArpt="{arr}">'
        f"<{body}><qualifiedAircraftId><aircraftId>{callsign}</aircraftId><gufi>G-T</gufi>"
        f"{igtd_xml}<departurePoint><airport>{dep}</airport></departurePoint>"
        f"<arrivalPoint><airport>{arr}</airport></arrivalPoint></qualifiedAircraftId></{body}>"
        "</fltdMessage>")


def test_tfms_leg_context_reads_airports_and_igtd():
    from ingest.parsers import tfms_parser as tp
    ctx = tp._leg_context(_fltd("FlightCreate", "AAL1893", "KFLL", "KDCA", "2026-10-11T18:00:00Z"))
    assert ctx == {"origin": "KFLL", "destination": "KDCA", "sched_dep": "2026-10-11T18:00:00Z"}
    assert tp._route_endpoints("KMCO.JEEMY4.PAINN..CAMJO...KJFK") == ("KMCO", "KJFK")
    assert tp._route_endpoints("PAINN..CAMJO") == (None, None)


def test_tfms_other_leg_of_a_shuttle_does_not_fire_the_watch(watches, monkeypatch):
    from ingest.parsers import tfms_parser as tp
    fired = []
    monkeypatch.setattr(tp, "_fire_tfms_watchlist_hit", lambda e, s, d, priority=3: fired.append(s))

    tp._handle_flight_create(_fltd("FlightCreate", "AAL1893", "KFLL", "KDCA", "2026-10-11T18:00:00Z"))
    assert fired == []                                   # afternoon return leg

    tp._handle_flight_create(_fltd("FlightCreate", "AAL1893", "KDCA", "KFLL", "2026-10-11T12:00:00Z"))
    assert len(fired) == 1                               # the watched morning leg


def test_tfms_matches_codeshare_operating_callsign_on_its_route_only(watches):
    from ingest.parsers import tfms_parser as tp
    db.upsert_codeshare_mapping("UAL", "4520", "GJS", "4520", "KDCA", "KORD", "test")
    db.upsert_codeshare_mapping("UAL", "4520", "RPA", "4520", "KEWR", "KDCA", "test")
    watches[:] = [{"id": "wl-flight-ual4520-test", "identifier": "UAL4520", "origin": "DCA",
                   "destination": "ORD", "scheduled_departure": None, "hex_id": None}]
    assert tp._match_watchlist_flight("GJS4520", origin="KDCA", destination="KORD")["id"] == \
        "wl-flight-ual4520-test"
    assert tp._match_watchlist_flight("RPA4520", origin="KEWR", destination="KDCA") is None


def test_tbfm_sequence_for_the_other_leg_is_ignored(watches, monkeypatch):
    from ingest.parsers import tbfm_parser as bp
    import shared.watchlist as wl
    hits = []
    monkeypatch.setattr(wl, "watchlist_event_hit", lambda eid, s, d, priority=3: hits.append(eid))
    monkeypatch.setattr(db, "update_watchlist_tbfm_status", lambda *a, **k: None)
    # watch is DCA->FLL; a TBFM arrival sequence INTO DCA for the same number
    # (from FLL) is the return leg
    watches[:] = [dict(MORNING)]
    bp._check_tbfm_watchlist_hits([{"flight_id": "AAL1893", "meter_fix": "TSTFX",
                                    "eta": "2026-10-11T20:00:00Z", "apt": "DCA", "dap": "KFLL",
                                    "facility": "ZDC"}])
    assert hits == []

    inbound = {"id": "wl-flight-aal1893-pm", "identifier": "AAL1893", "origin": "FLL",
               "destination": "DCA", "scheduled_departure": None, "hex_id": None}
    watches[:] = [dict(MORNING), inbound]
    bp._check_tbfm_watchlist_hits([{"flight_id": "AAL1893", "meter_fix": "TSTFX",
                                    "eta": "2026-10-11T20:00:00Z", "apt": "DCA", "dap": "KFLL",
                                    "facility": "ZDC"}])
    assert hits == ["wl-flight-aal1893-pm"]
