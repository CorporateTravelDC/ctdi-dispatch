"""2026-10-24: SWIM_STORAGE_SCOPE -- the SWIM storage filter as a deployment
setting. regional (default) is the untouched 250 nm + core-airport + core-WFO
behaviour; national accepts every SWIM record, keeps NWWS/WFO filtering
regional, and makes storage_scope() return the NAS form the map consumes.
The env is monkeypatched per test; points are public airport-area
coordinates (KLAX-ish, KDCA) and synthetic facility codes only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ingest.parsers import geo_filter as gf  # noqa: E402

FAR_WEST = (33.94, -118.41)      # ~2000 nm from DCA: out of the regional circle
NEAR_DCA = (38.95, -77.46)       # inside the 250 nm circle


@pytest.fixture
def regional(monkeypatch):
    monkeypatch.delenv("SWIM_STORAGE_SCOPE", raising=False)


@pytest.fixture
def national(monkeypatch):
    monkeypatch.setenv("SWIM_STORAGE_SCOPE", "national")


def test_default_is_regional_and_unchanged(regional):
    assert gf.swim_storage_scope() == "regional" and gf.is_national_scope() is False
    assert gf.in_range(*NEAR_DCA) and not gf.in_range(*FAR_WEST) and not gf.in_range(None, None)
    assert gf.passes_geo_filter(lat=FAR_WEST[0], lon=FAR_WEST[1]) is False
    assert gf.passes_geo_filter(airport="KLAX") is True            # core airport still passes
    assert gf.passes_geo_filter(airport="KXYZ") is False
    assert gf.passes_geo_filter(wfo="LWX") is True and gf.passes_geo_filter(wfo="ZZZ") is False
    sc = gf.storage_scope()
    assert sc["scope"] == "regional" and sc["radius_nm"] == 250.0 and sc["bounds"] is None
    assert sc["center"] == [gf._DCA_LAT, gf._DCA_LON] and "KDCA" in sc["airports"] and sc["label"]


def test_national_accepts_every_swim_record(national):
    assert gf.swim_storage_scope() == "national" and gf.is_national_scope() is True
    assert gf.in_range(*FAR_WEST) is True
    assert gf.in_range(None, None) is False                        # widens the area, never invents a position
    assert gf.passes_geo_filter(lat=FAR_WEST[0], lon=FAR_WEST[1]) is True
    assert gf.passes_geo_filter(airport="KXYZ") is True             # non-core facility stored too
    assert gf.passes_geo_filter() is True


def test_national_keeps_membership_predicates_and_wfo_regional(national):
    # pure predicates never change meaning
    assert gf.is_core_airport("KLAX") is True and gf.is_core_airport("KXYZ") is False
    # NWWS weather filtering is deliberately not scope-aware
    assert gf.is_core_wfo("LWX") is True and gf.is_core_wfo("KLWX") is True
    assert gf.is_core_wfo("ZZZ") is False and gf.is_core_wfo(None) is False


def test_national_storage_scope_is_the_nas_box(national):
    sc = gf.storage_scope()
    assert sc["scope"] == "national" and sc["center"] is None and sc["radius_nm"] is None
    assert sc["bounds"] == [[17.5, -179.5], [72.0, -60.0]] == [list(b) for b in gf.NAS_BOUNDS]
    assert sc["label"] == "NAS-wide (FAA SWIM)" == gf.NAS_LABEL
    assert sc["airports"] == sorted(a for a in gf.CORE_AIRPORTS if len(a) == 4)
    (s, w), (n, e) = sc["bounds"]
    assert s < gf._DCA_LAT < n and w < gf._DCA_LON < e           # the regional centre lies inside the box


@pytest.mark.parametrize("raw", ["", "  ", "NATIONWIDE", "both", "1"])
def test_unknown_value_falls_back_to_regional(monkeypatch, raw):
    monkeypatch.setenv("SWIM_STORAGE_SCOPE", raw)
    assert gf.swim_storage_scope() == "regional"
    assert gf.passes_geo_filter(lat=FAR_WEST[0], lon=FAR_WEST[1]) is False


def test_value_is_case_and_space_insensitive(monkeypatch):
    monkeypatch.setenv("SWIM_STORAGE_SCOPE", "  National ")
    assert gf.swim_storage_scope() == "national"


# -- the parsers' storage gates follow the setting --------------------------------

def test_fdps_in_dc_area_follows_scope(monkeypatch):
    from ingest.parsers import fdps_parser
    far = {"callsign": "TEST123", "latitude": FAR_WEST[0], "longitude": FAR_WEST[1],
           "origin": "KXYZ", "destination": "KABC"}
    monkeypatch.delenv("SWIM_STORAGE_SCOPE", raising=False)
    assert fdps_parser._in_dc_area(far) is False
    monkeypatch.setenv("SWIM_STORAGE_SCOPE", "national")
    assert fdps_parser._in_dc_area(far) is True


def test_tfms_write_gate_follows_scope(monkeypatch):
    from ingest.parsers import tfms_parser
    programs = [{"program_id": "GDP-XYZ-1", "type": "GDP", "facility": "XYZ",
                 "start_time": 1.0, "end_time": 4102444800.0}]
    with mock.patch.object(tfms_parser.db, "upsert_nas_program") as up, \
         mock.patch.object(tfms_parser.db, "find_legacy_nas_program", return_value=None, create=True):
        monkeypatch.delenv("SWIM_STORAGE_SCOPE", raising=False)
        assert tfms_parser.write_tfms_programs(programs) == 0 and up.call_count == 0
        monkeypatch.setenv("SWIM_STORAGE_SCOPE", "national")
        assert tfms_parser.write_tfms_programs(programs) == 1 and up.call_count == 1


def test_aim_write_gate_follows_scope_but_alert_gate_does_not(monkeypatch):
    from ingest.parsers import aim_parser
    notam = {"notam_id": "T-0001", "raw_json": "{}", "facility": "KXYZ", "classification": "DOM",
             "fir": "ZLA", "location": "XYZ", "text_body": "RWY 09/27 CLSD", "effective_start": None,
             "effective_end": None, "is_vip": False}
    with mock.patch.object(aim_parser.db, "upsert_notam") as up, \
         mock.patch.object(aim_parser, "_fire_notam_alert") as fire:
        monkeypatch.delenv("SWIM_STORAGE_SCOPE", raising=False)
        aim_parser.write_aim_notams([dict(notam)])
        assert up.call_count == 0 and fire.call_count == 0
        monkeypatch.setenv("SWIM_STORAGE_SCOPE", "national")
        aim_parser.write_aim_notams([dict(notam)])
        assert up.call_count == 1 and fire.call_count == 0         # stored nationwide, never alerted
