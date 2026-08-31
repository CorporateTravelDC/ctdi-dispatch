"""
Tests for the 2026-08-30 SWIM ingest audit changes, against REAL captured
live traffic (UNMODIFIED copies under tests/ingest/fixtures/swim_audit/,
copied 2026-08-30 from the box's own capture directories -- each test
names its sample's origin):

  1. FDPS MessageCollection batch fix -- parse_fdps_messages() must yield
     one dict per batched <message> child (the old first-message-only
     unwrap silently dropped everything past message[0]; the existing
     committed fixture TH_sample_21.xml is itself a real 16-message batch,
     so this suite's own fixtures already proved the bug).
  2. STDDS APDS/TDES additions -- RVR, tower departure events (gate
     numbers), TDLS clearance text, D-ATIS: parse + normalization rules.
  3. ITWS new product handlers -- Runway Configuration, Terminal Weather
     Text Normal/Special.
  4. TFMS FADT (per-flight EDCT slot broadcasts) -- parse + DDHHMM
     normalization + DC scoping.
  5. TBFM unknown-<air>-child diagnostic capture (the STA-claim probe).

Every test that touches a parse entry point with a debug-capture side
effect redirects the capture directory to tmp_path first -- unlike the
live capture dirs, tests must never write into /var/lib.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

FIXTURES = Path(__file__).parent / "fixtures" / "swim_audit"
FIXM30_REAL = Path(__file__).parent / "fixtures" / "fdps_fixm30_real"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _quiet_captures(monkeypatch, tmp_path):
    """Point every parser's debug-capture directory at tmp_path."""
    from ingest.parsers import fdps_parser, itws_parser, tfms_parser, tbfm_parser
    monkeypatch.setattr(fdps_parser, "_DEBUG_SAMPLE_DIR", str(tmp_path / "fdps"))
    monkeypatch.setattr(fdps_parser, "_DEBUG_SAMPLE_DIR_FIXM30", str(tmp_path / "fdps30"))
    monkeypatch.setattr(itws_parser, "_DEBUG_SAMPLE_DIR", str(tmp_path / "itws"))
    monkeypatch.setattr(itws_parser, "_PRODUCT_SAMPLE_DIR", str(tmp_path / "itws_prod"))
    monkeypatch.setattr(itws_parser, "_PARSE_FAILURE_DIR", str(tmp_path / "itws_fail"))
    monkeypatch.setattr(tfms_parser, "_DEBUG_SAMPLE_DIR", str(tmp_path / "tfms"))
    monkeypatch.setattr(tfms_parser, "_UNKNOWN_MSGTYPE_DIR", str(tmp_path / "tfms_unk"),
                        raising=False)
    monkeypatch.setattr(tbfm_parser, "_DEBUG_SAMPLE_DIR", str(tmp_path / "tbfm"))
    monkeypatch.setattr(tbfm_parser, "_UNKNOWN_KIND_DIR", str(tmp_path / "tbfm_unk"))


# ── 1. FDPS batch fix ────────────────────────────────────────────────────────

def test_fdps_batched_document_yields_every_message(monkeypatch, tmp_path):
    """TH_sample_21.xml is a REAL captured MessageCollection carrying 16
    message children (grep 'source=' -- it was committed as a fixture on
    2026-08-17 for the single-message tests, unknowingly carrying the
    batch-drop evidence the whole time). The batch-aware entry point must
    yield one parsed dict per child; the legacy single entry point keeps
    returning only the first."""
    _quiet_captures(monkeypatch, tmp_path)
    from ingest.parsers.fdps_parser import parse_fdps_message, parse_fdps_messages
    raw = (FIXM30_REAL / "TH_sample_21.xml").read_bytes()
    n_in_doc = raw.count(b'source="')
    assert n_in_doc == 16, "fixture changed -- update this test's premise"

    batch = parse_fdps_messages(raw)
    assert len(batch) == n_in_doc

    single = parse_fdps_message(raw)
    assert single is not None
    # first batched dict == singular dict, apart from raw_xml scoping
    d_batch = {k: v for k, v in batch[0].items() if k != "raw_xml"}
    d_single = {k: v for k, v in single.items() if k != "raw_xml"}
    assert d_batch == d_single


def test_fdps_batched_raw_xml_is_per_flight_and_reparseable(monkeypatch, tmp_path):
    """Each batched dict's raw_xml must be the per-flight element (not the
    348KB whole document repeated N times) and must re-parse cleanly --
    db._find_flight_element() reads it back out of flight_events."""
    _quiet_captures(monkeypatch, tmp_path)
    from ingest.parsers.fdps_parser import parse_fdps_messages
    raw = (FIXM30_REAL / "TH_sample_21.xml").read_bytes()
    for parsed in parse_fdps_messages(raw):
        assert len(parsed["raw_xml"]) < len(raw)
        root = ET.fromstring(parsed["raw_xml"])
        if parsed["callsign"]:
            assert parsed["callsign"] in parsed["raw_xml"]
        assert root is not None


def test_fdps_single_document_batch_parity(monkeypatch, tmp_path):
    """For every real single-message fixture, the batch entry point must
    return exactly [singular result] -- including a byte-identical
    whole-document raw_xml (pre-fix storage behavior preserved)."""
    _quiet_captures(monkeypatch, tmp_path)
    from ingest.parsers.fdps_parser import parse_fdps_message, parse_fdps_messages
    for f in sorted(FIXM30_REAL.glob("*.xml")):
        raw = f.read_bytes()
        if raw.count(b'source="') != 1:
            continue  # the batched fixture is covered above
        batch = parse_fdps_messages(raw)
        single = parse_fdps_message(raw)
        assert len(batch) == 1, f.name
        assert batch[0] == single, f.name
        assert batch[0]["raw_xml"] == raw.decode("utf-8", errors="replace"), f.name


# ── 2. STDDS APDS/TDES ───────────────────────────────────────────────────────

def test_rvr_parse_real_sample_units_and_null_rules():
    """Real capture: smes_debug/RVRDataUpdateMessage_0.xml (KFSD). Values
    arrive in hundreds of feet ('60' -> 6000 ft); blank means
    sensor-not-reporting and must become None, NEVER 0; live trend chars
    are +/-/blank and normalize to U/D/None."""
    from ingest.parsers.smes_parser import parse_tdes_apds_message
    rec = parse_tdes_apds_message(_load("RVRDataUpdateMessage_0.xml"))
    assert rec is not None and rec["kind"] == "rvr"
    assert rec["airport"] == "KFSD"
    assert len(rec["runways"]) == 2
    r0 = rec["runways"][0]
    assert r0["runway"] == "03"
    assert r0["touchdown_rvr_ft"] == 6000
    assert r0["touchdown_trend"] == "U"          # live '+' normalized
    assert r0["midpoint_rvr_ft"] is None          # blank in the real sample
    assert r0["midpoint_trend"] is None
    assert r0["rollout_rvr_ft"] == 6000


def test_rvr_zero_and_garbage_values_are_null():
    from ingest.parsers.smes_parser import _rvr_value_ft, _rvr_trend
    assert _rvr_value_ft("00") is None    # dead sensor, not zero visibility
    assert _rvr_value_ft("  ") is None
    assert _rvr_value_ft(None) is None
    assert _rvr_value_ft("P60") is None   # non-numeric shape -> refuse to guess
    assert _rvr_value_ft("06") == 600
    assert _rvr_value_ft("60") == 6000
    assert _rvr_trend("+") == "U" and _rvr_trend("-") == "D"
    assert _rvr_trend(" ") is None and _rvr_trend(None) is None
    assert _rvr_trend("S") == "S"


def test_tdes_departure_event_parse_real_sample():
    """Real capture: smes_debug/TowerDepartureEventMessage_0.xml -- JSX8501
    at KDAL, parking gate 'G', clearance-delivery time, both GUFIs."""
    from ingest.parsers.smes_parser import parse_tdes_apds_message
    rec = parse_tdes_apds_message(_load("TowerDepartureEventMessage_0.xml"))
    assert rec is not None and rec["kind"] == "tdes_departure"
    assert rec["airport"] == "KDAL"
    assert rec["callsign"] == "JSX8501"
    assert rec["parking_gate"] == "G"
    assert rec["clearance_delivery_time"] == "2026-08-30T12:45:44.000Z"
    assert rec["eram_gufi"] == "KF369411ti"
    assert rec["sfdps_gufi"]
    assert rec["destination_airport"] == "KT89"


def test_tdls_parse_real_sample_timestamp_format():
    """Real capture: smes_debug/TDLSCSPMessage_0.xml -- UAL1803 at KPIT.
    The <time> field is MMDDYYYYHHMMSS (08302026124553 == 2026-08-30
    12:45:53Z, matching the capture time)."""
    from ingest.parsers.smes_parser import parse_tdes_apds_message
    rec = parse_tdes_apds_message(_load("TDLSCSPMessage_0.xml"))
    assert rec is not None and rec["kind"] == "tdls"
    assert rec["airport"] == "KPIT"
    assert rec["callsign"] == "UAL1803"
    assert rec["message_time"] == "2026-08-30T12:45:53Z"
    assert "CPDLC DCL DISPATCH MSG" in rec["data_body"]
    assert rec["destination_airport"] == "KORD"


def test_datis_parse_real_sample():
    """Real capture: smes_debug/DATISData_0.xml -- KLGA ATIS info U, body
    carries the active runway config in prose."""
    from ingest.parsers.smes_parser import parse_tdes_apds_message
    rec = parse_tdes_apds_message(_load("DATISData_0.xml"))
    assert rec is not None and rec["kind"] == "datis"
    assert rec["airport"] == "KLGA"
    assert rec["atis_code"] == "U"
    assert "LND RY 22" in rec["body"]


def test_apds_dispatcher_ignores_other_roots():
    from ingest.parsers.smes_parser import parse_tdes_apds_message
    assert parse_tdes_apds_message(b"<STDDSStatus><x/></STDDSStatus>") is None
    assert parse_tdes_apds_message(b"not xml at all") is None


def test_apds_handle_scopes_to_dc_area():
    """Non-DC, non-watchlisted records must not be stored (nationwide
    stream, bounded growth). DB layer + watchlist are mocked -- storage
    behavior only."""
    from ingest.parsers import smes_parser
    rec = smes_parser.parse_tdes_apds_message(_load("RVRDataUpdateMessage_0.xml"))
    assert rec["airport"] == "KFSD"  # not DC
    with patch("common.db_swim.upsert_stdds_rvr") as up:
        assert smes_parser.handle_tdes_apds_record(rec) is False
        up.assert_not_called()
    rec["airport"] = "KIAD"
    with patch("common.db_swim.upsert_stdds_rvr") as up:
        assert smes_parser.handle_tdes_apds_record(rec) is True
        assert up.call_count == 2  # one per runway


# ── 3. ITWS new products ─────────────────────────────────────────────────────

def test_itws_runway_configuration_product(monkeypatch, tmp_path):
    """Real capture: itws_debug_by_product/Runway_Configuration_Product.xml
    (PCT/IAD, config IAD-19L-19C-12). Severity 0 -- state row, no push."""
    _quiet_captures(monkeypatch, tmp_path)
    from ingest.parsers.itws_parser import parse_itws_message
    alerts = parse_itws_message(_load("Runway_Configuration_Product.xml"))
    assert len(alerts) == 1
    a = alerts[0]
    assert a["airport"] == "KIAD"
    assert a["product_type"] == "Runway Configuration Product"
    assert a["severity"] == 0
    assert "IAD-19L-19C-12" in a["detail"]


def test_itws_terminal_weather_text_products(monkeypatch, tmp_path):
    """Real captures: Normal (KIAD, '-NO STORM WITHIN 15NM' -> quiet
    severity 0) and Special (KDCA, 'WSA CANC' -- NOT a no-storm line, so
    severity 3: recorded, below the push threshold of 4)."""
    _quiet_captures(monkeypatch, tmp_path)
    from ingest.parsers.itws_parser import parse_itws_message
    normal = parse_itws_message(_load("Terminal_Weather_Text_Normal_Product.xml"))
    assert len(normal) == 1
    assert normal[0]["airport"] == "KIAD" and normal[0]["severity"] == 0
    assert "NO STORM" in normal[0]["detail"]

    special = parse_itws_message(_load("Terminal_Weather_Text_Special_Product.xml"))
    assert len(special) == 1
    assert special[0]["airport"] == "KDCA" and special[0]["severity"] == 3
    assert "WSA CANC" in special[0]["detail"]


# ── 4. TFMS FADT ─────────────────────────────────────────────────────────────

def test_fadt_real_sample_stores_only_dc_relevant_slots(monkeypatch, tmp_path):
    """Real capture: tfms_debug_unknown_msgtype/FADT_0.xml -- an EWR
    ground-stop slot list with 9 slots, exactly one of which (UAL8115,
    IAD->EWR) touches a DC-area airport. Only that slot may be stored;
    EDCT DDHHMM '030516' must normalize to 2026-08-03T05:16:00Z using the
    broadcast's own reportTime month."""
    _quiet_captures(monkeypatch, tmp_path)
    from ingest.parsers import tfms_parser
    stored = []
    with patch("common.db_swim.upsert_tfms_edct_slot",
               side_effect=lambda **kw: stored.append(kw)), \
         patch("shared.watchlist.get_active_entries", return_value=[]):
        programs = tfms_parser.parse_tfms_message(_load("FADT_0.xml"))
    assert programs == []  # FADT never contributes nas_programs rows
    assert len(stored) == 1
    s = stored[0]
    assert s["aircraft_id"] == "UAL8115"
    assert s["control_element"] == "EWR"
    assert s["departure_airport"] == "IAD"
    assert s["arrival_airport"] == "EWR"
    assert s["program_parameter"] == "GS"
    assert s["controlled_departure_time"] == "030516"
    assert s["controlled_departure_iso"] == "2026-08-03T05:16:00Z"
    assert s["exempt_flag"] == 0 and s["cancel_flag"] == 0


def test_fadt_ddhhmm_month_rollover():
    from ingest.parsers.tfms_parser import _fadt_ddhhmm_to_iso
    # slot on the 1st, report on the 31st -> next month
    assert _fadt_ddhhmm_to_iso("010030", "2026-08-31T23:50:00Z") == "2026-09-01T00:30:00Z"
    # slot on the 31st, report on the 1st -> previous month
    assert _fadt_ddhhmm_to_iso("312350", "2026-09-01T00:10:00Z") == "2026-08-31T23:50:00Z"
    # year boundary both ways
    assert _fadt_ddhhmm_to_iso("010030", "2026-12-31T23:50:00Z") == "2027-01-01T00:30:00Z"
    assert _fadt_ddhhmm_to_iso("312350", "2027-01-01T00:10:00Z") == "2026-12-31T23:50:00Z"
    # malformed shapes refuse rather than guess
    assert _fadt_ddhhmm_to_iso("9999", "2026-08-03T04:19:36Z") is None
    assert _fadt_ddhhmm_to_iso("326000", "2026-08-03T04:19:36Z") is None
    assert _fadt_ddhhmm_to_iso(None, "2026-08-03T04:19:36Z") is None
    assert _fadt_ddhhmm_to_iso("030516", None) is None


# ── 5. TBFM unknown-<air>-child capture ──────────────────────────────────────

def test_tbfm_unknown_air_kind_capture(monkeypatch, tmp_path):
    """A synthetic <sta>-carrying message (the external document's claimed
    schedule shape -- NEVER observed in a real capture on this box) must
    land in the unknown-kind capture dir and produce no sequence rows; a
    real captured flt/eta message must produce no capture."""
    _quiet_captures(monkeypatch, tmp_path)
    from ingest.parsers import tbfm_parser
    monkeypatch.setattr(tbfm_parser, "_unknown_kind_counts", {})
    sta_msg = (
        b'<?xml version="1.0"?>'
        b'<env xmlns="urn:us:gov:dot:faa:atm:tfm:tbfmmeteringpublication:1.1.0"'
        b' envSrce="TMA.ZDC.FAA.GOV" envTime="2026-08-30T12:00:00Z">'
        b'<tma msgTime="2026-08-30T12:00:00Z" msgId="1">'
        b'<air gufi="X" apt="DCA" dap="BOS" aid="TEST1">'
        b'<sta><mfx>WAVER</mfx><sta_rwy>2026-08-30T12:30:00Z</sta_rwy></sta>'
        b'</air></tma></env>'
    )
    assert tbfm_parser.parse_tbfm_message(sta_msg) == []
    unk = tmp_path / "tbfm_unk"
    assert (unk / "sta_0.xml").exists()
    assert (unk / "sta_0.xml").read_bytes() == sta_msg

    # a normal eta-carrying message adds no captures
    eta_msg = sta_msg.replace(b"<sta>", b"<eta>").replace(b"</sta>", b"</eta>") \
                     .replace(b"sta_rwy", b"eta_rwy")
    seqs = tbfm_parser.parse_tbfm_message(eta_msg)
    assert len(seqs) == 1 and seqs[0]["meter_fix"] == "WAVER"
    assert sorted(p.name for p in unk.iterdir()) == ["sta_0.xml"]
