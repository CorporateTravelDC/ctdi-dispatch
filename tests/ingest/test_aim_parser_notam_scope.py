"""
Regression tests for NOTAM storage scoping (2026-07-17).

Bug: write_aim_notams() unconditionally stored every FDC-classification NOTAM
nationwide (`is_fdc` bypassed the geo filter entirely), so the notams table
filled with airshow/TFR NOTAMs from anywhere in the country. Of 4,578 rows
the live /api/v1/notams endpoint was returning as "active", only 239 were
actually DC-area.

Fix: FDC NOTAMs are now must-ingest only when tied to a DC-region ARTCC
(ZDC/ZNY/ZID/ZTL/ZOB) or when the text reads as a nationally significant
event (CFR 91.137/141/143/145, 99.7, or airshow/closure/VIP keywords). VIP
NOTAMs (POTUS/VP/AF1/AF2/Marine One) remain must-ingest nationwide regardless
of classification or facility.

These tests assert the new _in_dc_region / _is_national_significant helpers
and the write_aim_notams storage gate behave as specified.
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import ingest.parsers.aim_parser as aim_parser


# ── _in_dc_region ────────────────────────────────────────────────────────────

def test_in_dc_region_true_for_zdc_fir():
    notam = {"fir": "ZDC", "location": "IAD", "facility": "KIAD"}
    assert aim_parser._in_dc_region(notam) is True


def test_in_dc_region_true_for_cleveland_artcc():
    notam = {"fir": "ZOB", "location": "ZOB", "facility": ""}
    assert aim_parser._in_dc_region(notam) is True


def test_in_dc_region_true_for_k_prefixed_artcc_code():
    # Some FNS extensions wrap ARTCC codes with a pseudo-ICAO K-prefix.
    notam = {"fir": "", "location": "", "facility": "KZTL"}
    assert aim_parser._in_dc_region(notam) is True


def test_in_dc_region_false_for_unrelated_artcc():
    notam = {"fir": "ZMP", "location": "ZMP", "facility": "KZMP"}
    assert aim_parser._in_dc_region(notam) is False


def test_in_dc_region_false_when_no_fields():
    assert aim_parser._in_dc_region({}) is False


# ── _is_national_significant ────────────────────────────────────────────────

def test_national_significant_true_for_cfr_91_145():
    text = "PURSUANT TO 14 CFR SECTION 91.145, AERIAL DEMONSTRATION..."
    assert aim_parser._is_national_significant(text) is True


def test_national_significant_true_for_airshow_keyword():
    text = "TEMPORARY FLIGHT RESTRICTIONS DUE TO AIR SHOW ACTIVITY"
    assert aim_parser._is_national_significant(text) is True


def test_national_significant_false_for_routine_text():
    text = "TAXIWAY BRAVO CLOSED FOR MAINTENANCE 0700-1500 DAILY"
    assert aim_parser._is_national_significant(text) is False


def test_national_significant_false_for_empty_text():
    assert aim_parser._is_national_significant("") is False


# ── _is_vip_notam (extended keywords) ───────────────────────────────────────

def test_vip_notam_true_for_vice_president():
    assert aim_parser._is_vip_notam("VPOTUS MOVEMENT EXPECTED") is True


def test_vip_notam_true_for_air_force_two():
    assert aim_parser._is_vip_notam("AIR FORCE TWO ARRIVAL") is True


def test_vip_notam_false_for_routine_text():
    assert aim_parser._is_vip_notam("RUNWAY 01/19 CLOSED") is False


# ── write_aim_notams storage gate ───────────────────────────────────────────

def _base_notam(**overrides):
    n = {
        "notam_id": "TEST/2026/0001",
        "facility": "KZZZ",
        "location": "",
        "fir": "",
        "classification": "NOTAM-D",
        "effective_start": None,
        "effective_end": None,
        "text_body": "ROUTINE NOTAM TEXT",
        "raw_json": "{}",
    }
    n.update(overrides)
    return n


def _patched():
    return mock.patch.multiple(
        aim_parser,
        db=mock.DEFAULT,
        _get_transient_airports=mock.DEFAULT,
        _get_facility_filter=mock.DEFAULT,
        is_core_airport=mock.DEFAULT,
        _fire_notam_alert=mock.DEFAULT,
        _maybe_cleanup_expired=mock.DEFAULT,
    )


def test_dc_region_fdc_notam_stored_even_outside_watch_set():
    with _patched() as p:
        p["_get_transient_airports"].return_value = frozenset()
        p["_get_facility_filter"].return_value = frozenset({"KDCA", "KIAD", "KBWI"})
        p["is_core_airport"].return_value = False
        n = _base_notam(classification="FDC", facility="KZZZ", fir="ZDC",
                         text_body="ROUTINE FDC ENTRY, NOT DC AIRPORT FACILITY")
        written = aim_parser.write_aim_notams([n])
    assert written == 1
    p["db"].upsert_notam.assert_called_once()
    p["_fire_notam_alert"].assert_called_once()


def test_nationwide_fdc_airshow_stored_but_not_alerted():
    with _patched() as p:
        p["_get_transient_airports"].return_value = frozenset()
        p["_get_facility_filter"].return_value = frozenset({"KDCA", "KIAD", "KBWI"})
        p["is_core_airport"].return_value = False
        n = _base_notam(classification="FDC", facility="KFSD", fir="ZMP",
                         text_body="PURSUANT TO 14 CFR SECTION 91.145 AIR SHOW TFR")
        written = aim_parser.write_aim_notams([n])
    assert written == 1
    p["db"].upsert_notam.assert_called_once()
    p["_fire_notam_alert"].assert_not_called()


def test_nationwide_fdc_routine_dropped():
    with _patched() as p:
        p["_get_transient_airports"].return_value = frozenset()
        p["_get_facility_filter"].return_value = frozenset({"KDCA", "KIAD", "KBWI"})
        p["is_core_airport"].return_value = False
        n = _base_notam(classification="FDC", facility="KFCM", fir="ZMP",
                         text_body="ROUTINE FDC AMENDMENT, NOTHING NOTABLE")
        written = aim_parser.write_aim_notams([n])
    assert written == 0
    p["db"].upsert_notam.assert_not_called()


def test_nationwide_notam_d_outside_watch_dropped():
    with _patched() as p:
        p["_get_transient_airports"].return_value = frozenset()
        p["_get_facility_filter"].return_value = frozenset({"KDCA", "KIAD", "KBWI"})
        p["is_core_airport"].return_value = False
        n = _base_notam(classification="NOTAM-D", facility="KAEX", fir="ZHU",
                         text_body="TAXIWAY CLOSED")
        written = aim_parser.write_aim_notams([n])
    assert written == 0


def test_vip_notam_stored_and_alerted_regardless_of_facility():
    with _patched() as p:
        p["_get_transient_airports"].return_value = frozenset()
        p["_get_facility_filter"].return_value = frozenset({"KDCA", "KIAD", "KBWI"})
        p["is_core_airport"].return_value = False
        n = _base_notam(classification="FDC", facility="KFSD", fir="ZMP",
                         text_body="MARINE ONE MOVEMENT EXPECTED")
        written = aim_parser.write_aim_notams([n])
    assert written == 1
    p["_fire_notam_alert"].assert_called_once()


def test_write_aim_notams_calls_throttled_cleanup():
    with _patched() as p:
        p["_get_transient_airports"].return_value = frozenset()
        p["_get_facility_filter"].return_value = frozenset({"KDCA"})
        p["is_core_airport"].return_value = True
        n = _base_notam(facility="KDCA")
        aim_parser.write_aim_notams([n])
    p["_maybe_cleanup_expired"].assert_called_once()


# ── _normalize_notam_number ─────────────────────────────────────────────────

def test_normalize_notam_number_strips_leading_zeros():
    assert aim_parser._normalize_notam_number("006") == "6"


def test_normalize_notam_number_unchanged_for_bare_number():
    assert aim_parser._normalize_notam_number("6") == "6"


def test_normalize_notam_number_non_numeric_passthrough():
    assert aim_parser._normalize_notam_number("6A") == "6A"


def test_normalize_notam_number_empty():
    assert aim_parser._normalize_notam_number("") == ""
    assert aim_parser._normalize_notam_number(None) == ""


# ── DC-region bypass is FDC-only (regression, 2026-07-17) ──────────────────
#
# _in_dc_region must-ingest was originally applied to *any* classification,
# which meant every routine NOTAM-D anywhere inside ZID/ZTL/ZOB/ZNY (each of
# which covers a huge chunk of the Midwest/Northeast, not just "near DC")
# became a must-ingest+alert item. Confirmed live: 164 NOTAM-D rows from
# small regional fields (Louisville KSDF, etc.) flooded nas-alerts this way.
# Corey's actual ask was scoped to FDC ("on the FDC thing... anything within
# ZDC/ZNY/ZID/ZTL/ZOB must ingest") -- NOTAM-D keeps the original, narrower
# core-airport/watch-set-only gate.

def test_notam_d_in_dc_region_artcc_dropped_if_not_core_or_watch():
    with _patched() as p:
        p["_get_transient_airports"].return_value = frozenset()
        p["_get_facility_filter"].return_value = frozenset({"KDCA", "KIAD", "KBWI"})
        p["is_core_airport"].return_value = False
        n = _base_notam(classification="NOTAM-D", facility="KSDF", fir="ZID",
                         text_body="AD AP ABN U/S")
        written = aim_parser.write_aim_notams([n])
    assert written == 0
    p["db"].upsert_notam.assert_not_called()


def test_fdc_in_dc_region_artcc_still_stored():
    with _patched() as p:
        p["_get_transient_airports"].return_value = frozenset()
        p["_get_facility_filter"].return_value = frozenset({"KDCA", "KIAD", "KBWI"})
        p["is_core_airport"].return_value = False
        n = _base_notam(classification="FDC", facility="KSDF", fir="ZID",
                         text_body="ROUTINE FDC ENTRY TIED TO ZID FIR")
        written = aim_parser.write_aim_notams([n])
    assert written == 1
    p["db"].upsert_notam.assert_called_once()
