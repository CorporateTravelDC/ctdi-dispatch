"""Tests for Marine One / POTUS proximity and callsign detection."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

FIXTURES = Path(__file__).parent / "fixtures"


def test_marine_one_callsign_in_set():
    from ingest.parsers.fdps_parser import is_marine_one
    assert is_marine_one("MARINE1", None)
    assert is_marine_one("MARINE2", None)
    assert is_marine_one("AF1", None)
    assert is_marine_one("AF2", None)
    assert is_marine_one("AZAZ01", None)


def test_marine_one_squawk():
    from ingest.parsers.fdps_parser import is_marine_one
    assert is_marine_one(None, "5000")
    assert is_marine_one(None, "5001")
    assert is_marine_one("UNKNOWN", "5000")


def test_non_marine_one():
    from ingest.parsers.fdps_parser import is_marine_one
    assert not is_marine_one("AAL123", "1234")
    assert not is_marine_one("UAL456", None)
    assert not is_marine_one(None, None)


def test_distance_to_dca_near():
    from ingest.parsers.fdps_parser import distance_to_dca_nm, MARINE_ONE_RADIUS_NM
    # DCA itself should be ~0 nm away
    dist = distance_to_dca_nm(38.8522, -77.0376)
    assert dist < 0.1


def test_distance_to_dca_far():
    from ingest.parsers.fdps_parser import distance_to_dca_nm, MARINE_ONE_RADIUS_NM
    # New York JFK is ~200nm from DCA
    dist = distance_to_dca_nm(40.6413, -73.7781)
    assert dist > MARINE_ONE_RADIUS_NM


def test_marine_one_detection_within_radius():
    """Position inside 50nm of DCA with Marine One callsign should trigger detection."""
    from ingest.parsers.fdps_parser import parse_fdps_message, check_marine_one
    xml = (FIXTURES / "fdps_marine_one.xml").read_bytes()
    parsed = parse_fdps_message(xml)
    assert parsed is not None
    assert parsed["callsign"] == "MARINE1"
    assert parsed["latitude"] == 38.9
    assert parsed["longitude"] == -77.02
    # check_marine_one requires DB — just test the distance guard.
    from ingest.parsers.fdps_parser import distance_to_dca_nm, MARINE_ONE_RADIUS_NM
    dist = distance_to_dca_nm(parsed["latitude"], parsed["longitude"])
    assert dist < MARINE_ONE_RADIUS_NM


def test_marine_one_detection_outside_radius():
    """Marine One callsign far from DCA should NOT trigger when source is TH."""
    from ingest.parsers.fdps_parser import is_marine_one, distance_to_dca_nm, MARINE_ONE_RADIUS_NM
    # Marine One over Chicago
    lat, lon = 41.9742, -87.9073
    assert is_marine_one("MARINE1", None)
    dist = distance_to_dca_nm(lat, lon)
    assert dist > MARINE_ONE_RADIUS_NM  # would be filtered by radius check


def test_haversine_symmetry():
    from ingest.parsers.fdps_parser import _haversine_nm
    d1 = _haversine_nm(38.85, -77.04, 39.18, -76.67)
    d2 = _haversine_nm(39.18, -76.67, 38.85, -77.04)
    assert abs(d1 - d2) < 0.001


def test_smes_parser_basic():
    """Basic SMES position report parse."""
    from ingest.parsers.smes_parser import parse_smes_message
    xml = (FIXTURES / "smes_position.xml").read_bytes()
    tracks = parse_smes_message(xml)
    assert len(tracks) == 1
    t = tracks[0]
    assert t["airport"] == "KDCA"
    assert t["track_id"] == "4501"
    assert t["callsign"] == "AAL256"
    assert t["squawk"] == "3456"
    assert t["latitude"] == 38.8499
    assert t["longitude"] == -77.035
    assert t["aircraft_type"] == "B738"


def test_smes_parser_wrong_airport():
    """SMES message for non-monitored airport should return empty list."""
    from ingest.parsers.smes_parser import parse_smes_message
    xml = b"""<?xml version="1.0"?>
    <positionReport airport="KSFO" trackNumber="9999">
      <aircraftIdentification>UAL1</aircraftIdentification>
      <latitude>37.6213</latitude>
      <longitude>-122.379</longitude>
    </positionReport>"""
    tracks = parse_smes_message(xml)
    assert tracks == []
