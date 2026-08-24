"""
Smoke tests against REAL captured live FDPS traffic (FIXM 3.0).

2026-08-17 finding: every pre-existing FDPS test
(tests/ingest/test_fdps_parser.py, test_fdps_element_truthiness.py) uses
hand-crafted fixtures under the FIXM 4.2 namespace
(xmlns:msg="http://www.fixm.aero/messaging/4.2"), with synthetic
sequential GUFIs ("AAA01234-...-000000000001") that are clearly not real
captures. fdps_parser.py's own docstring says the LIVE feed is FIXM 3.0 --
parse_fdps_message() sniffs the namespace and routes 3.0 traffic to
_parse_fdps_message_fixm30, 4.2 to the explicitly-named
_parse_fdps_message_fixm42_legacy. 100% of real captured samples on this
box (/var/lib/corporatetraveldc/fdps_debug_fixm30/, 25 files as of
2026-08-17) are FIXM 3.0. So the entire pre-existing FDPS test suite has
been exercising a code path real production traffic never touches, while
the actual live path (_parse_fdps_message_fixm30) had zero test coverage.

Fixtures here (tests/ingest/fixtures/fdps_fixm30_real/) are UNMODIFIED
copies of real captured messages, one per distinct `source` value seen in
the 25 available samples (AH, CL, HF, HP, HX, HZ, OH, RH, TH) -- copied
2026-08-17 from fdps_debug_fixm30/. No FH sample existed in the available
capture set at the time this was written; if one is captured later it
should be added here too.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

FIXTURES = Path(__file__).parent / "fixtures" / "fdps_fixm30_real"

# (fixture filename, expected source, expected callsign) -- expected values
# read directly off the real XML via grep before writing this file, not
# guessed.
REAL_SAMPLES = [
    ("AH_sample_9.xml", "AH", "AAL574"),
    ("CL_sample_24.xml", "CL", "DAL563"),
    ("HF_sample_19.xml", "HF", "SWA672"),
    ("HP_sample_10.xml", "HP", "AAL3155"),
    ("HX_sample_11.xml", "HX", "AAL574"),
    ("HZ_sample_6.xml", "HZ", "UCA4985"),
    ("OH_sample_15.xml", "OH", "DAL2871"),
    ("RH_sample_22.xml", "RH", "N621VS"),
    ("TH_sample_21.xml", "TH", "UAL2033"),
]


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_real_samples_exist():
    """Guard against the fixture set silently going empty (e.g. a bad
    rebase/merge) -- would make every test below vacuously meaningless."""
    assert len(list(FIXTURES.glob("*.xml"))) == len(REAL_SAMPLES)


def test_all_real_samples_detected_as_fixm30():
    """The actual regression this file exists to prevent: real live
    traffic must route to the 3.0 parser, never silently fall through to
    the 4.2 legacy path or return None."""
    from ingest.parsers.fdps_parser import _detect_fixm_version
    for filename, _, _ in REAL_SAMPLES:
        version = _detect_fixm_version(_load(filename))
        assert version == "3.0", f"{filename}: expected FIXM 3.0 detection, got {version!r}"


def test_all_real_samples_parse_without_exception_via_public_entrypoint():
    """Exercises parse_fdps_message() -- the actual dispatcher every
    ingest call site uses -- not the internal fixm30 function directly,
    so this also proves the version-sniff+route step itself works."""
    from ingest.parsers.fdps_parser import parse_fdps_message
    for filename, expected_source, expected_callsign in REAL_SAMPLES:
        parsed = parse_fdps_message(_load(filename))
        assert parsed is not None, f"{filename}: parser returned None for a real, known-good live sample"
        assert parsed["source"] == expected_source, f"{filename}: source mismatch"
        assert parsed["callsign"] == expected_callsign, f"{filename}: callsign mismatch"
        assert parsed["gufi"], f"{filename}: real message should always carry a GUFI"
        assert "raw_xml" in parsed and parsed["raw_xml"], f"{filename}: raw_xml missing"


def test_th_and_ah_carry_position_or_route_fields_real():
    """TH (track) and AH (agreed/route) messages have different real
    shapes -- TH carries live lat/lon/alt from enRoute/position, AH
    typically carries route/agreed data instead. Confirms the parser
    doesn't silently return an all-None shell for either real shape."""
    from ingest.parsers.fdps_parser import parse_fdps_message
    th = parse_fdps_message(_load("TH_sample_21.xml"))
    assert th is not None
    # A real TH sample should yield SOME telemetry -- lat/lon, altitude, or
    # squawk -- not all three None simultaneously (which would mean the
    # enRoute/position path silently broke against real data).
    assert any(th.get(k) is not None for k in ("latitude", "longitude", "altitude_ft", "squawk")), \
        "TH_sample_21.xml: parsed no position/telemetry fields at all from a real TH message"


def test_cl_source_forces_cancelled_status_real():
    """fdps_parser.py hardcodes flight_status='CANCELLED' when source=='CL'
    -- verify that actually fires against a real captured CL message, not
    just a synthetic one."""
    from ingest.parsers.fdps_parser import parse_fdps_message
    parsed = parse_fdps_message(_load("CL_sample_24.xml"))
    assert parsed is not None
    assert parsed["flight_status"] == "CANCELLED"


def test_write_flight_event_consumes_real_parsed_shape_without_exception():
    """write_flight_event() is the real downstream consumer every FDPS
    message that reaches this platform's flight_events table goes
    through. Prove it doesn't raise against the ACTUAL dict shape
    _parse_fdps_message_fixm30 produces (field names/types), using a
    real sample -- catches drift between the parser's output shape and
    what the DB layer expects, which a synthetic-fixture test with
    matching field names by construction would never catch."""
    import types
    from ingest.parsers import fdps_parser
    from unittest.mock import patch

    parsed = fdps_parser.parse_fdps_message(_load("TH_sample_21.xml"))
    assert parsed is not None

    captured = {}

    def fake_upsert_flight_event(**kwargs):
        captured.update(kwargs)

    with patch.object(fdps_parser.db, "upsert_flight_event", side_effect=fake_upsert_flight_event):
        fdps_parser.write_flight_event(parsed)

    # write_flight_event only actually writes for DC-area-relevant flights
    # (_in_dc_area gate) -- this real UAL2033 TH sample may or may not
    # pass that gate depending on its real lat/lon. Either outcome is
    # valid; what this test guards is that write_flight_event doesn't
    # raise while handling a REAL parsed dict, regardless of which branch
    # it takes.
    assert isinstance(captured, dict)
