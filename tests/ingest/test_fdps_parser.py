"""Unit tests for the FDPS FIXM XML parser."""
import sys
from pathlib import Path

# Make src/ importable without installation.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_fh_fields():
    from ingest.parsers.fdps_parser import parse_fdps_message
    parsed = parse_fdps_message(_load("fdps_fh.xml"))
    assert parsed is not None
    assert parsed["source"] == "FH"
    assert parsed["callsign"] == "JIA5438"
    assert parsed["origin"] == "KCVG"
    assert parsed["destination"] == "KPHL"
    assert parsed["aircraft_type"] == "CRJ9"
    assert parsed["flight_status"] == "ACTIVE"
    assert parsed["latitude"] is None   # FH has no position
    assert parsed["longitude"] is None


def test_th_fields():
    from ingest.parsers.fdps_parser import parse_fdps_message
    parsed = parse_fdps_message(_load("fdps_th.xml"))
    assert parsed is not None
    assert parsed["source"] == "TH"
    assert parsed["callsign"] == "JIA5438"
    assert parsed["latitude"] == 40.12
    assert parsed["longitude"] == -76.58
    assert parsed["altitude_ft"] == 28000.0
    assert parsed["ground_speed"] == 420
    assert parsed["squawk"] == "1234"


def test_cl_fields():
    from ingest.parsers.fdps_parser import parse_fdps_message
    parsed = parse_fdps_message(_load("fdps_cl.xml"))
    assert parsed is not None
    assert parsed["source"] == "CL"
    assert parsed["flight_status"] == "CANCELLED"
    assert parsed["callsign"] == "JIA5438"


def test_invalid_xml_returns_none():
    from ingest.parsers.fdps_parser import parse_fdps_message
    assert parse_fdps_message(b"<not valid xml") is None


def test_empty_bytes_returns_none():
    from ingest.parsers.fdps_parser import parse_fdps_message
    assert parse_fdps_message(b"") is None


def test_unhandled_source_returns_none():
    xml = b"""<?xml version="1.0"?>
    <msg:Message xmlns:msg="http://www.fixm.aero/messaging/4.2"
                 xmlns:fx="http://www.fixm.aero/flight/4.2"
                 xmlns:nas="http://www.fixm.aero/nas/4.2">
      <msg:flight>
        <fx:gufi>test-gufi</fx:gufi>
        <nas:nasFlightInfo source="XX"><nas:acid>TST001</nas:acid></nas:nasFlightInfo>
      </msg:flight>
    </msg:Message>"""
    from ingest.parsers.fdps_parser import parse_fdps_message
    assert parse_fdps_message(xml) is None


def test_raw_xml_preserved():
    from ingest.parsers.fdps_parser import parse_fdps_message
    raw = _load("fdps_fh.xml")
    parsed = parse_fdps_message(raw)
    assert parsed is not None
    assert "JIA5438" in parsed["raw_xml"]
