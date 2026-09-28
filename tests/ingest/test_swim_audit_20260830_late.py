"""
Tests for the 2026-08-30 LATE pass (external SWIM diversion-detection
document applied to the detectors that went live today): the operator-
class gate on the continuation detector (store-not-alert for fractional/
GA), net-change collapse for both the continuation and alternate-
saturation detectors (flap oscillation, multi-hop originally-filed
destination), the O==D (Trap 5) guard, growth-only re-fire for alternate
saturation, and the diversionIndicator closed-vocabulary split
(AIRBORN_* vs GROUND_* vs unknown).

Same discipline as the morning/afternoon/evening/night files: isolated
temp DB, DISPATCH_STATE_DIR pointed at tmp_path wherever a PushDedup is
touched, pushes patched. SYNTHETIC fixtures throughout: zero real
diversions exist in fdps_destination_changes to date (the detector's
entire first live day was spelling-flap noise), and no real TFMS capture
has ever carried a non-quiet diversionIndicator -- every scenario here is
constructed, clearly labeled as such, and derived from the external
document's described populations (EJA/LXJ fractional callsigns, N-number
GA, KPHL<->KPIT-style destination oscillation).
"""
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


def _isolated_db():
    import common.db as db
    from common import db_swim
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig = db._db_path
    db._db_path = lambda: Path(tmp.name)
    db.init_db_all()
    db_swim.init_db_swim_v41()
    db_swim.init_db_swim_v42()
    db_swim.init_db_swim_v44()
    db_swim.init_db_swim_v45()
    return orig, tmp.name


def _restore_db(orig, tmp_name):
    import common.db as db
    db._db_path = orig
    Path(tmp_name).unlink(missing_ok=True)


def _seed_diverted_flight(flight_id: str, callsign: str, origin: str,
                          old_dest: str, new_dest: str,
                          registration: str | None = None):
    """Same seeding shape as the night-pass file: one flight_events row
    plus a destination-change observation."""
    import common.db as db
    from common import db_swim
    db.upsert_flight_event(
        flight_id=flight_id, airline=callsign[:3], flight_num=callsign[3:],
        origin=origin, destination=new_dest, aircraft_type=None,
        departure_time=None, arrival_time=None, status=None,
        position_lat=None, position_lon=None, altitude_ft=None,
        ground_speed_kt=None, raw_json="")
    if registration:
        db_swim.update_flight_event_extras(
            flight_id=flight_id, squawk=None, registration=registration,
            controlling_facility=None)
    db_swim.insert_fdps_destination_change(
        flight_id=flight_id, callsign=callsign, origin=origin,
        old_destination=old_dest, new_destination=new_dest, source="FH")


def _rows(sql: str):
    from common.db import conn
    with conn() as c:
        return [dict(r) for r in c.execute(sql).fetchall()]


# ── Operator-class helper ────────────────────────────────────────────────────

def test_operator_class_helper():
    from ingest.parsers.fdps_parser import _operator_class
    # fractional/charter designators fly airline-SHAPED callsigns and
    # pass a tail-number filter -- the document's "one most likely missed"
    assert _operator_class("EJA744") == "fractional"     # NetJets
    assert _operator_class("LXJ452") == "fractional"     # Flexjet
    assert _operator_class("eja744") == "fractional"     # case-insensitive
    # tail-number GA
    assert _operator_class("N863WA") == "ga_tail"
    assert _operator_class("N1") == "ga_tail"
    assert _operator_class("N12345") == "ga_tail"
    # scheduled carriers (the only alertable class)
    assert _operator_class("UAL123") == "scheduled"
    assert _operator_class("SWA9") == "scheduled"
    assert _operator_class("DAL2") == "scheduled"
    # edge shapes fail OPEN to scheduled (the airport-pair + relationship
    # match is still required before anything fires)
    assert _operator_class(None) == "scheduled"
    assert _operator_class("") == "scheduled"
    assert _operator_class("EJA") == "scheduled"   # bare designator, no flight number
    # NJE1 lacks a digit-at-4? "NJE1" -> len 4, cs[3]='1'.isdigit() -> fractional
    assert _operator_class("NJE1") == "fractional"


# ── Operator gate on continuation pairs ──────────────────────────────────────

def test_continuation_fractional_pair_stored_not_alerted():
    """SYNTHETIC: EJA744 (NetJets) 'diverts' KPBI->KRSW then files
    KRSW->KPBI -- a routine fractional multi-leg re-file per the
    document (~85% of raw candidates). The pair row is STORED with
    operator_class='fractional' but NO alert fires."""
    orig, tmp = _isolated_db()
    try:
        from ingest.parsers import fdps_parser
        _seed_diverted_flight("GUFI-FRAC1", "EJA744", "KTEB", "KPBI", "KRSW")
        with patch("shared.sector_coalesce.fire_family_alert") as fire:
            fdps_parser._check_diversion_continuation(
                {"origin": "KRSW", "destination": "KPBI"},
                "GUFI-FRAC1-CONT", "EJA744")
        rows = _rows("SELECT * FROM fdps_diversion_continuations")
        assert len(rows) == 1
        assert rows[0]["operator_class"] == "fractional"
        assert not fire.called
    finally:
        _restore_db(orig, tmp)


def test_continuation_ga_tail_pair_stored_not_alerted():
    """SYNTHETIC: N863WA tail-number GA -- same store-not-alert path."""
    orig, tmp = _isolated_db()
    try:
        from ingest.parsers import fdps_parser
        _seed_diverted_flight("GUFI-GA1", "N863WA", "KSJC", "KMFR", "KRDD")
        with patch("shared.sector_coalesce.fire_family_alert") as fire:
            fdps_parser._check_diversion_continuation(
                {"origin": "KRDD", "destination": "KMFR"},
                "GUFI-GA1-CONT", "N863WA")
        rows = _rows("SELECT * FROM fdps_diversion_continuations")
        assert len(rows) == 1
        assert rows[0]["operator_class"] == "ga_tail"
        assert not fire.called
    finally:
        _restore_db(orig, tmp)


def test_continuation_scheduled_pair_still_alerts():
    """SYNTHETIC: the same shape on a scheduled carrier (UAL123) must
    still fire exactly one alert -- the gate excludes classes, never the
    genuine population."""
    orig, tmp = _isolated_db()
    try:
        from ingest.parsers import fdps_parser
        _seed_diverted_flight("GUFI-SCHED1", "UAL123", "KIAD", "KBOS", "KBDL")
        fired = []
        with patch("shared.sector_coalesce.fire_family_alert",
                   side_effect=lambda *a, **k: fired.append(a)):
            fdps_parser._check_diversion_continuation(
                {"origin": "KBDL", "destination": "KBOS"},
                "GUFI-SCHED1-CONT", "UAL123")
        rows = _rows("SELECT * FROM fdps_diversion_continuations")
        assert len(rows) == 1
        assert rows[0]["operator_class"] == "scheduled"
        assert len(fired) == 1
    finally:
        _restore_db(orig, tmp)


def test_continuation_gate_falls_back_to_diverted_callsign():
    """SYNTHETIC: continuation matched by REGISTRATION under a garbled/
    absent callsign while the DIVERTED leg flew as EJA -- the gate
    classifies via the diverted side and still suppresses the alert."""
    orig, tmp = _isolated_db()
    try:
        from ingest.parsers import fdps_parser
        _seed_diverted_flight("GUFI-FRAC2", "EJA512", "KHPN", "KMCO", "KDAB",
                              registration="N512QS")
        with patch("shared.sector_coalesce.fire_family_alert") as fire:
            fdps_parser._check_diversion_continuation(
                {"origin": "KDAB", "destination": "KMCO",
                 "registration": "N512QS"},
                "GUFI-FRAC2-CONT", None)
        rows = _rows("SELECT * FROM fdps_diversion_continuations")
        assert len(rows) == 1
        assert rows[0]["match_basis"] == "registration"
        assert rows[0]["operator_class"] == "fractional"
        assert not fire.called
    finally:
        _restore_db(orig, tmp)


# ── Net-change collapse (document Trap 2) ────────────────────────────────────

def test_continuation_flap_back_home_never_pairs():
    """SYNTHETIC oscillation (document's KPHL->KPIT->KPHL example): UAL500
    changed KBOS->KBDL then KBDL->KBOS. Net change is a no-op, so a later
    KBDL->KBOS filing by UAL500 must NOT pair -- under per-row matching
    the stale KBOS->KBDL row would have seeded a false continuation."""
    orig, tmp = _isolated_db()
    try:
        from common import db_swim
        from ingest.parsers import fdps_parser
        _seed_diverted_flight("GUFI-FLAP1", "UAL500", "KIAD", "KBOS", "KBDL")
        db_swim.insert_fdps_destination_change(
            flight_id="GUFI-FLAP1", callsign="UAL500", origin="KIAD",
            old_destination="KBDL", new_destination="KBOS", source="FH")
        with patch("shared.sector_coalesce.fire_family_alert") as fire:
            fdps_parser._check_diversion_continuation(
                {"origin": "KBDL", "destination": "KBOS"},
                "GUFI-FLAP1-CONT", "UAL500")
        assert _rows("SELECT * FROM fdps_diversion_continuations") == []
        assert not fire.called
    finally:
        _restore_db(orig, tmp)


def test_continuation_multi_hop_matches_originally_filed_destination():
    """SYNTHETIC multi-hop amendment: UAL600 filed ->KBOS, amended ->KBDL,
    amended again ->KALB. The document's chaining rule condition 3: the
    continuation's destination must be the ORIGINALLY FILED destination
    (KBOS -- earliest old value), never the intermediate (KBDL)."""
    orig, tmp = _isolated_db()
    try:
        from common import db_swim
        from ingest.parsers import fdps_parser
        _seed_diverted_flight("GUFI-HOP1", "UAL600", "KIAD", "KBOS", "KBDL")
        db_swim.insert_fdps_destination_change(
            flight_id="GUFI-HOP1", callsign="UAL600", origin="KIAD",
            old_destination="KBDL", new_destination="KALB", source="FH")
        fired = []
        with patch("shared.sector_coalesce.fire_family_alert",
                   side_effect=lambda *a, **k: fired.append(a)):
            # intermediate destination: must NOT pair
            fdps_parser._check_diversion_continuation(
                {"origin": "KALB", "destination": "KBDL"},
                "GUFI-HOP1-X", "UAL600")
            assert _rows("SELECT * FROM fdps_diversion_continuations") == []
            # originally filed destination: pairs, from the divert airport
            fdps_parser._check_diversion_continuation(
                {"origin": "KALB", "destination": "KBOS"},
                "GUFI-HOP1-CONT", "UAL600")
        rows = _rows("SELECT * FROM fdps_diversion_continuations")
        assert len(rows) == 1
        assert rows[0]["original_destination"] == "BOS"
        assert rows[0]["diversion_airport"] == "ALB"
        assert len(fired) == 1
    finally:
        _restore_db(orig, tmp)


def test_continuation_trap5_origin_equals_destination_never_pairs():
    """SYNTHETIC Trap 5: a leg FILED KTEB->KTEB (maintenance/positioning)
    that re-points to KBDL must never seed a 'continuation' KBDL->KTEB --
    that would just be the return hop of a check flight."""
    orig, tmp = _isolated_db()
    try:
        from ingest.parsers import fdps_parser
        _seed_diverted_flight("GUFI-MX1", "UAL700", "KTEB", "KTEB", "KBDL")
        with patch("shared.sector_coalesce.fire_family_alert") as fire:
            fdps_parser._check_diversion_continuation(
                {"origin": "KBDL", "destination": "KTEB"},
                "GUFI-MX1-CONT", "UAL700")
        assert _rows("SELECT * FROM fdps_diversion_continuations") == []
        assert not fire.called
    finally:
        _restore_db(orig, tmp)


# ── Alternate saturation: net collapse + growth-only re-fire ─────────────────

def _seed_alt_change(fid: str, cs: str, origin: str, old: str, new: str):
    from common import db_swim
    db_swim.insert_fdps_destination_change(
        flight_id=fid, callsign=cs, origin=origin,
        old_destination=old, new_destination=new, source="FH")


def test_alt_saturation_growth_fires_shrinkage_stays_quiet(monkeypatch, tmp_path):
    """SYNTHETIC convergence on KBDL: 4 flights re-file to it -> one
    alert. A flight then flapping BACK to its original destination
    shrinks the net set to 3 (still >= threshold): under the old per-row
    + content-key-only logic that shrunken set was a 'new' content key
    and re-fired; the subset gate must keep it quiet. A genuinely NEW
    5th flight re-fires (growth is the signal)."""
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    orig, tmp = _isolated_db()
    try:
        from ingest.parsers import fdps_parser
        fdps_parser._ALT_SAT_LAST_ALERTED.clear()
        for i, cs in enumerate(["UAL1", "AAL2", "DAL3", "JBU4"]):
            _seed_alt_change(f"GUFI-SAT{i}", cs, "KIAD", "KBOS", "KBDL")
        fired = []
        with patch("shared.sector_coalesce.fire_family_alert",
                   side_effect=lambda *a, **k: fired.append(a)):
            fdps_parser._check_alternate_saturation("KBDL")
            assert len(fired) == 1
            assert "4 flights" in fired[0][3]  # title (args: family, feed, facility, title, ...)
            # identical re-check: quiet
            fdps_parser._check_alternate_saturation("KBDL")
            assert len(fired) == 1
            # JBU4 flaps home -> net set shrinks to 3 -> must stay quiet
            _seed_alt_change("GUFI-SAT3", "JBU4", "KIAD", "KBDL", "KBOS")
            fdps_parser._check_alternate_saturation("KBDL")
            assert len(fired) == 1
            # genuinely new 5th flight -> growth -> re-fires
            _seed_alt_change("GUFI-SAT4", "SWA5", "KDCA", "KPVD", "KBDL")
            fdps_parser._check_alternate_saturation("KBDL")
            assert len(fired) == 2
    finally:
        fdps_parser._ALT_SAT_LAST_ALERTED.clear()
        _restore_db(orig, tmp)


def test_alt_saturation_flap_rows_do_not_reach_threshold(monkeypatch, tmp_path):
    """SYNTHETIC: three flights each oscillate ->KBDL then back home.
    Net-collapsed, NOBODY is converging on KBDL; the detector must not
    fire even though 3 raw rows in the window say new_destination=KBDL."""
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    orig, tmp = _isolated_db()
    try:
        from ingest.parsers import fdps_parser
        fdps_parser._ALT_SAT_LAST_ALERTED.clear()
        for i, cs in enumerate(["UAL11", "AAL12", "DAL13"]):
            _seed_alt_change(f"GUFI-FLP{i}", cs, "KIAD", "KBOS", "KBDL")
            _seed_alt_change(f"GUFI-FLP{i}", cs, "KIAD", "KBDL", "KBOS")
        with patch("shared.sector_coalesce.fire_family_alert") as fire:
            fdps_parser._check_alternate_saturation("KBDL")
        assert not fire.called
    finally:
        fdps_parser._ALT_SAT_LAST_ALERTED.clear()
        _restore_db(orig, tmp)


# ── diversionIndicator closed vocabulary (tfms_parser) ───────────────────────

def test_diversion_indicator_classification():
    from ingest.parsers.tfms_parser import _classify_diversion_indicator
    assert _classify_diversion_indicator(None) == (False, None)
    assert _classify_diversion_indicator("") == (False, None)
    assert _classify_diversion_indicator("NO_DIVERSION") == (False, None)
    assert _classify_diversion_indicator("no_diversion") == (False, None)
    assert _classify_diversion_indicator("AIRBORN_NOCTL") == (True, "airborne")
    assert _classify_diversion_indicator("AIRBORN_CTL") == (True, "airborne")
    assert _classify_diversion_indicator("GROUND_NOCTL") == (True, "ground")
    assert _classify_diversion_indicator("GROUND_CTL") == (True, "ground")


def test_diversion_indicator_unknown_member_warns_once(caplog):
    import logging
    from ingest.parsers import tfms_parser
    tfms_parser._UNKNOWN_DIVERSION_VALUES_SEEN.discard("ORBITAL_DIVERT")
    with caplog.at_level(logging.WARNING, logger="ingest.parsers.tfms"):
        assert tfms_parser._classify_diversion_indicator("ORBITAL_DIVERT") == (True, "unknown")
        first = [r for r in caplog.records if "ORBITAL_DIVERT" in r.getMessage()]
        assert len(first) == 1
        # second sighting: still flagged, no second warning
        assert tfms_parser._classify_diversion_indicator("ORBITAL_DIVERT") == (True, "unknown")
        again = [r for r in caplog.records if "ORBITAL_DIVERT" in r.getMessage()]
        assert len(again) == 1


def _flight_route_xml(indicator: str) -> bytes:
    """SYNTHETIC fltdMessage (no real capture carries a non-quiet
    diversionIndicator) -- local-name shapes match the real FlightRoute_0
    capture's element layout."""
    return f"""
    <fltdMessage msgType="FlightRoute" sourceFacility="ZDC">
      <ncsmFlightRoute>
        <qualifiedAircraftId>
          <aircraftId>UAL123</aircraftId>
          <gufi>SYN-GUFI-1</gufi>
          <departurePoint><airport>IAD</airport></departurePoint>
          <arrivalPoint><airport>BOS</airport></arrivalPoint>
        </qualifiedAircraftId>
        <ncsmRouteData>
          <diversionIndicator>{indicator}</diversionIndicator>
          <star routeName="ROBUC3" routeType="STAR"/>
        </ncsmRouteData>
      </ncsmFlightRoute>
    </fltdMessage>""".encode()


def test_flight_route_airborne_vs_ground_priority_and_label():
    from ingest.parsers import tfms_parser
    entry = {"id": "wl-late-1", "identifier": "UAL123"}
    hits = []
    with patch("shared.watchlist.get_active_entries", return_value=[entry]), \
         patch.object(tfms_parser, "_fire_tfms_watchlist_hit",
                      side_effect=lambda *a, **k: hits.append((a, k))):
        tfms_parser._handle_flight_route(
            ET.fromstring(_flight_route_xml("AIRBORN_NOCTL")))
        tfms_parser._handle_flight_route(
            ET.fromstring(_flight_route_xml("GROUND_NOCTL")))
        tfms_parser._handle_flight_route(
            ET.fromstring(_flight_route_xml("NO_DIVERSION")))
    assert len(hits) == 3
    (a_args, a_kw), (g_args, g_kw), (q_args, q_kw) = hits
    assert "AIRBORNE DIVERSION" in a_args[1]
    assert a_kw["priority"] == 4
    assert a_args[2]["diversion_kind"] == "airborne"
    assert "GROUND RE-FILE" in g_args[1]
    assert g_kw["priority"] == 3
    assert g_args[2]["diversion_kind"] == "ground"
    # quiet indicator: normal route hit, no diversion label
    assert "DIVERSION" not in q_args[1] and "RE-FILE" not in q_args[1]
    assert q_kw["priority"] == 3
    assert q_args[2]["diversion_kind"] is None
