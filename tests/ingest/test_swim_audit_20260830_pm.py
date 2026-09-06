"""
Tests for the 2026-08-30 AFTERNOON pass (SWIM-audit backlog items built
on top of the morning audit): TFMS PARAM delay statistics, TFMS REROUTE
advisory storage, and TDLS PDC/DCL body parsing. Same discipline as
test_swim_audit_20260830.py: every fixture is an UNMODIFIED real capture
from this box (tfms_debug_unknown_msgtype/, smes_debug/), and each test
names its sample. All DB assertions run against an isolated temp DB with
the real v41+v42 schema applied -- never /var/lib.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

FIXTURES = Path(__file__).parent / "fixtures" / "swim_audit"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


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
    return orig, tmp.name


def _restore_db(orig, tmp_name):
    import common.db as db
    db._db_path = orig
    Path(tmp_name).unlink(missing_ok=True)


def _quiet_tfms(monkeypatch, tmp_path):
    from ingest.parsers import tfms_parser
    monkeypatch.setattr(tfms_parser, "_DEBUG_SAMPLE_DIR", str(tmp_path / "tfms"),
                        raising=False)
    monkeypatch.setattr(tfms_parser, "_UNKNOWN_MSGTYPE_DIR",
                        str(tmp_path / "tfms_unk"), raising=False)


# ── TFMS PARAM ───────────────────────────────────────────────────────────────

def test_param_gs_variant_stores_delay_stats(monkeypatch, tmp_path):
    """PARAM_0.xml: real paramGsUpdt for the 2026-08-27 EWR ground stop --
    55 flights modeled, 5 affected, avg delay 0 -> 37.6 min."""
    _quiet_tfms(monkeypatch, tmp_path)
    import common.db as db
    from ingest.parsers.tfms_parser import parse_tfms_message
    orig, tmp_name = _isolated_db()
    try:
        assert parse_tfms_message(_load("PARAM_0.xml")) == []
        with db.conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM tfms_param_delay_stats").fetchall()]
        assert len(rows) == 1
        row = rows[0]
        assert (row["elem_name"], row["parameters_type"], row["tmi_state"]) == \
            ("EWR", "GS", "ACTUAL")
        assert row["total_flights"] == 55
        assert row["affected_flights"] == 5
        assert row["max_delay_after_min"] == 70
        assert row["avg_delay_before_min"] == 0.0
        assert row["avg_delay_after_min"] == 37.6
        assert row["impacting_condition_code"] == "5006"
        assert row["event_start_time"] == "2026-08-27T13:59:00Z"
        assert row["delay_mode"] is None      # GS variant has no delayMode
    finally:
        _restore_db(orig, tmp_name)


def test_param_afpgdp_proposed_never_overwrites_actual(monkeypatch, tmp_path):
    """PARAM_1.xml: real paramAfpGdpUpdt, SAN GDP in PROPOSED state
    (model run: 358 flights, 305 affected, delayMode UDP). tmi_state is
    in the storage key, so a PROPOSED row coexists with -- rather than
    clobbers -- an ACTUAL row for the same program (both states were
    observed live for this same SAN GDP)."""
    _quiet_tfms(monkeypatch, tmp_path)
    import common.db as db
    from common import db_swim
    from ingest.parsers.tfms_parser import parse_tfms_message
    orig, tmp_name = _isolated_db()
    try:
        # Pre-seed an ACTUAL row for the same (SAN, GDP) program.
        db_swim.upsert_tfms_param_delay_stats(
            "SAN", "GDP", "ACTUAL", "APT", "SAN", None, None, None, None,
            None, 999, 999, None, None, None, None, None, None, None, None,
            last_seen="2026-08-30T00:00:00Z")
        parse_tfms_message(_load("PARAM_1.xml"))
        with db.conn() as c:
            rows = {r["tmi_state"]: dict(r) for r in c.execute(
                "SELECT * FROM tfms_param_delay_stats WHERE elem_name='SAN'"
            ).fetchall()}
        assert set(rows) == {"ACTUAL", "PROPOSED"}
        assert rows["ACTUAL"]["total_flights"] == 999       # untouched
        assert rows["PROPOSED"]["total_flights"] == 358
        assert rows["PROPOSED"]["affected_flights"] == 305
        assert rows["PROPOSED"]["delay_mode"] == "UDP"
        assert rows["PROPOSED"]["total_delay_after_min"] == 13863
    finally:
        _restore_db(orig, tmp_name)


# ── TFMS REROUTE ─────────────────────────────────────────────────────────────

def test_reroute_stores_general_data_and_waypoint_free_segments(monkeypatch, tmp_path):
    """REROUTE_1.xml: real SERBOS_1_PARTIAL advisory (2026-08-30, KACK ->
    KEWR scope, 5 segments). General data lands keyed on FAA's rerouteId;
    the segment summary keeps origin/destin lists + route strings and
    drops every waypoint; not DC-relevant (ZBW/ZNY only)."""
    _quiet_tfms(monkeypatch, tmp_path)
    import common.db as db
    from ingest.parsers.tfms_parser import parse_tfms_message
    orig, tmp_name = _isolated_db()
    try:
        assert parse_tfms_message(_load("REROUTE_1.xml")) == []
        with db.conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM tfms_reroutes").fetchall()]
        assert len(rows) == 1
        row = rows[0]
        assert row["reroute_id"] == "rr.dccops.lxstn29.20260830145218"
        assert row["reroute_name"] == "SERBOS_1_PARTIAL"
        assert row["reroute_status"] == "ACTIVE"
        assert row["tmi_id"] == "RRDCC059"
        assert row["time_type"] == "ETD"
        assert row["start_time"] == "2026-08-30T14:45:00Z"
        assert row["segment_count"] == 5
        assert row["dc_relevant"] == 0
        segs = json.loads(row["segments_json"])
        assert len(segs) == 5
        assert segs[0]["origins"] == ["KACK", "ZBW", "ZNY"]
        assert segs[0]["destins"] == ["KEWR", "ZBW", "ZNY"]
        assert segs[0]["route"].startswith("ACK V146 PVD")
        # the waypoint-free guarantee: no lat/lon anywhere in the summary
        assert "latitude" not in row["segments_json"].lower()
        assert "waypoint" not in row["segments_json"].lower()
    finally:
        _restore_db(orig, tmp_name)


def test_reroute_dc_relevance_flags_zdc(monkeypatch, tmp_path):
    """REROUTE_13.xml: real JFK_THRU_ZDC_RRTE_MONITOR advisory whose
    segment origin/destin lists carry center ZDC -- must precompute
    dc_relevant=1, and a rebroadcast (same rerouteId) must upsert, not
    duplicate."""
    _quiet_tfms(monkeypatch, tmp_path)
    import common.db as db
    from ingest.parsers.tfms_parser import parse_tfms_message
    orig, tmp_name = _isolated_db()
    try:
        parse_tfms_message(_load("REROUTE_13.xml"))
        parse_tfms_message(_load("REROUTE_13.xml"))   # rebroadcast
        with db.conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM tfms_reroutes").fetchall()]
        assert len(rows) == 1
        assert rows[0]["dc_relevant"] == 1
        assert rows[0]["fca_name"] == "FCA014"
    finally:
        _restore_db(orig, tmp_name)


# ── TDLS body parsing ────────────────────────────────────────────────────────

def test_tdls_body_kslc_dcl_full_extraction():
    """TDLSCSPMessage_2.xml: real KSLC DAL2282 CPDLC DCL with the richest
    observed body -- WILCO response, MODIFIED RTE, MAINT 9000FT, DEP FREQ
    135.5, EXPECT RWY 16L, full route. Parsed through the real XML entry
    point so envelope handling and body extraction are covered together."""
    from ingest.parsers.smes_parser import parse_tdes_apds_message
    rec = parse_tdes_apds_message(_load("TDLSCSPMessage_2.xml"))
    assert rec is not None and rec["kind"] == "tdls"
    assert rec["airport"] == "KSLC" and rec["callsign"] == "DAL2282"
    p = rec["parsed"]
    assert p["dcl_type"] == "CPDLC_DCL"
    assert p["response_type"] == "WILCO"
    assert p["registration"] == "N369NB"
    assert p["expected_runway"] == "16L"
    assert p["initial_altitude_ft"] == 9000
    assert p["cruise_fl"] == "FL330"
    assert p["dep_frequency"] == "135.5"
    assert p["proposed_dep_time"] == "1710"
    assert p["route_text"] == "KSLC.RUGGD3.HOLTR..ELLKK..MOSSS..KJAC"
    assert p["sid"] == "RUGGD3"
    assert p["sid_transition"] == "HOLTR"
    assert p["climb_via_sid"] is None
    assert p["cleared_to"] is None        # MODIFIED RTE shape, no CLEARED TO
    assert p["edct_time"] is None         # no EDCT in this body


def test_tdls_body_kpit_revised_edct_and_routeless_sid():
    """TDLSCSPMessage_0.xml (the morning pass's own committed capture):
    real UAL1803 KPIT body carrying 'REVISED EDCT 1330' -- the only
    observed EDCT so far -- and the SID as a standalone token ('PIT5
    CLIMB VIA SID') with NO dotted route string in the body at all."""
    from ingest.parsers.smes_parser import parse_tdes_apds_message
    rec = parse_tdes_apds_message(_load("TDLSCSPMessage_0.xml"))
    p = rec["parsed"]
    assert p["dcl_type"] == "CPDLC_DCL"
    assert p["edct_time"] == "1330"
    assert p["sid"] == "PIT5"
    assert p["sid_transition"] is None
    assert p["route_text"] is None
    assert p["climb_via_sid"] == 1
    assert p["registration"] == "N17455"
    assert p["cruise_fl"] == "FL360"
    assert p["dep_frequency"] == "119.35"
    assert p["proposed_dep_time"] == "1100"
    assert p["response_type"] is None


def test_tdls_body_kiad_modified_rte_variant():
    """TDLSCSPMessage_kiad_ual1952.xml (real smes_debug capture, KIAD
    UAL1952): MAINT 3000FT but 'DEP FREQ SEE SID' (no frequency to
    extract), full dotted route KIAD.JCOBY4...KCHS."""
    from ingest.parsers.smes_parser import parse_tdes_apds_message
    rec = parse_tdes_apds_message(_load("TDLSCSPMessage_kiad_ual1952.xml"))
    p = rec["parsed"]
    assert p["response_type"] == "WILCO"
    assert p["registration"] == "N14735"
    assert p["initial_altitude_ft"] == 3000
    assert p["dep_frequency"] is None     # "SEE SID" is not a frequency
    assert p["cruise_fl"] == "FL300"
    assert p["proposed_dep_time"] == "1720"
    assert p["route_text"] == "KIAD.JCOBY4.SCOOB..DFENC.Q109.JOHAR..AMYLU.AMYLU3.KCHS"
    assert p["sid"] == "JCOBY4"
    assert p["sid_transition"] == "SCOOB"


def test_tdls_body_cleared_to_and_climb_via_sid():
    """Real body captured live into tdls_messages row 2 (KMIA SWA3740,
    2026-08-30T16:55:24Z) -- the only observed 'CLEARED TO ... AIRPORT' +
    'CLIMB VIA SID' shape; airway right after the transition must not be
    mistaken for one."""
    from ingest.parsers.smes_parser import parse_tdls_dcl_body
    body = ("003 CPDLC DCL DISPATCH MSG - NOT TO BE USED AS A CLEARANCE "
            "SWA3740 KMIA B738/L P1725 /AN N8569Z 00Y FL370 CLEARED TO "
            "KBWI AIRPORT ALTNN2.DUCEN THEN AS FILED CLIMB VIA SID   "
            "DEP FREQ 126.85 GROUND CTRL FREQ 121.8 "
            "KMIA.ALTNN2.DUCEN.Q87.RAYVO.Q113.AARNN..THHMP.RAVNN9.KBWI")
    p = parse_tdls_dcl_body(body)
    assert p["cleared_to"] == "KBWI"
    assert p["climb_via_sid"] == 1
    assert p["initial_altitude_ft"] is None
    assert p["dep_frequency"] == "126.85"
    assert p["sid"] == "ALTNN2"
    assert p["sid_transition"] == "DUCEN"
    assert p["route_text"].endswith("RAVNN9.KBWI")


def test_tdls_body_revised_rte_asterisked_origin():
    """Real body captured live into tdls_messages row 5 (KMIA AAL861 ->
    KDCA, 2026-08-30): a REVISED RTE marks the origin airport with an
    asterisk ('KMIA*.FOLZZ3...'), which must not break route/SID
    extraction."""
    from ingest.parsers.smes_parser import parse_tdls_dcl_body
    body = ("007 AAL861 KMIA /AN N873NN PILOT RESPONSE - WILCO 006 CPDLC "
            "DCL DISPATCH MSG - NOT TO BE USED AS A CLEARANCE AAL861 KMIA "
            "REVISED RTE DPP B738/L P1735 /AN N873NN 32A FL290 REVISED RTE "
            "KMIA*.FOLZZ3.ALYRA..GRUBR.Y299.SEELO..GARIC..RANAY..TANJA.."
            "WAVES.CAPSS4.KDCA REVISED DPP FOLZZ3.ALYRA CLIMB VIA SID "
            "DEP FREQ 126.85 GROUND CTRL FREQ 121.8")
    p = parse_tdls_dcl_body(body)
    assert p["route_text"].startswith("KMIA*.FOLZZ3")
    assert p["route_text"].endswith("CAPSS4.KDCA")
    assert p["sid"] == "FOLZZ3"
    assert p["sid_transition"] == "ALYRA"
    assert p["climb_via_sid"] == 1
    assert p["cruise_fl"] == "FL290"


def test_tdls_body_non_clearance_shape_parses_to_all_none():
    """TDLSCSPMessage_4.xml-shaped administrative body ('AAL501 001
    N303RG KPHX GA26') -- and None/garbage -- must yield all-None without
    raising; the raw body is stored verbatim regardless."""
    from ingest.parsers.smes_parser import parse_tdls_dcl_body
    for body in ("AAL501 001 N303RG KPHX GA26 ", None, "", "\x00\x01 ???"):
        p = parse_tdls_dcl_body(body)
        assert p["dcl_type"] is None
        assert p["sid"] is None and p["route_text"] is None
        assert p["initial_altitude_ft"] is None
    # the bare tail number in the admin shape must not match the /AN rule
    assert parse_tdls_dcl_body("AAL501 001 N303RG KPHX GA26")["registration"] is None


def test_tdls_parsed_fields_roundtrip_to_db():
    """insert_tdls_message(parsed=...) lands every v42 column; a call
    WITHOUT parsed (pre-v42 signature) still works and leaves them NULL."""
    import common.db as db
    from common import db_swim
    from ingest.parsers.smes_parser import parse_tdes_apds_message
    orig, tmp_name = _isolated_db()
    try:
        rec = parse_tdes_apds_message(_load("TDLSCSPMessage_2.xml"))
        db_swim.insert_tdls_message(
            airport=rec["airport"], callsign=rec["callsign"],
            message_time=rec["message_time"], beacon_code=rec["beacon_code"],
            aircraft_type=rec["aircraft_type"], computer_id=rec["computer_id"],
            data_header=rec["data_header"], data_body=rec["data_body"],
            eram_gufi=rec["eram_gufi"], sfdps_gufi=rec["sfdps_gufi"],
            destination_airport=rec["destination_airport"],
            received_at=rec["last_seen"], parsed=rec["parsed"])
        db_swim.insert_tdls_message(
            "KDCA", "TEST1", None, None, None, None, None, "raw only",
            None, None, None, "2026-08-30T00:00:00Z")
        with db.conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM tdls_messages ORDER BY id").fetchall()]
        assert rows[0]["sid"] == "RUGGD3"
        assert rows[0]["expected_runway"] == "16L"
        assert rows[0]["initial_altitude_ft"] == 9000
        assert rows[0]["data_body"]          # raw body always kept verbatim
        assert rows[1]["sid"] is None and rows[1]["data_body"] == "raw only"
    finally:
        _restore_db(orig, tmp_name)


def test_init_db_swim_v42_is_idempotent():
    import common.db as db
    from common import db_swim
    orig, tmp_name = _isolated_db()
    try:
        db_swim.init_db_swim_v42()   # second run: duplicate-column tolerant
        db_swim.init_db_swim_v42()
        with db.conn() as c:
            cols = {r["name"] for r in c.execute(
                "PRAGMA table_info(tdls_messages)").fetchall()}
        assert {"dcl_type", "sid", "expected_runway", "edct_time",
                "route_text"} <= cols
    finally:
        _restore_db(orig, tmp_name)
