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


def test_vip_notam_false_for_af1_af2_facility_identifiers():
    # 2026-09-27 regression: AF1/AF2 taxiway/stand/ramp identifiers must not
    # bleed into VIP detection (was substring-matched, routed to hot-alerts p5).
    assert aim_parser._is_vip_notam("!DCA 09/123 DCA TWY AF1 CLSD") is False
    assert aim_parser._is_vip_notam("!IAD 09/145 IAD ACFT STANDS AF1 THRU AF2 CLSD") is False


def test_vip_notam_true_for_air_force_2_numeral():
    # numeral form must still flag (added alongside the AF1/AF2 removal)
    assert aim_parser._is_vip_notam("AIR FORCE 2 ARRIVAL IAD") is True


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


def test_nationwide_fdc_airshow_stored_and_alerted_in_the_sweep():
    """2026-10-05 (operator): every authority-cited TFR is in the alert sweep;
    a 91.145 airshow outside the home zone alerts at priority 3 (tfr_priority)."""
    with _patched() as p:
        p["_get_transient_airports"].return_value = frozenset()
        p["_get_facility_filter"].return_value = frozenset({"KDCA", "KIAD", "KBWI"})
        p["is_core_airport"].return_value = False
        n = _base_notam(classification="FDC", facility="KFSD", fir="ZMP",
                         text_body="PURSUANT TO 14 CFR SECTION 91.145 AIR SHOW TFR")
        written = aim_parser.write_aim_notams([n])
    assert written == 1
    p["db"].upsert_notam.assert_called_once()
    p["_fire_notam_alert"].assert_called_once()
    assert aim_parser.tfr_priority(n) == 3


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
# the operator's actual ask was scoped to FDC ("on the FDC thing... anything within
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


# ── CRANE: callsign only (2026-10-05) ─────────────────────────────────────────

def test_crane_callsigns_are_vip():
    for t in ("CRANE01 ARR KADW 1500Z", "CRANE 05 DEP KIAD", "MOVEMENT OF CRANE50 AND CRANE 01",
              "TFR FOR CRANE 50 OPS"):
        assert aim_parser._is_vip_notam(t) is True, t


def test_crane_obstructions_and_bare_word_are_not_vip():
    for t in ("TEMPORARY CRANE 474 MSL 734FT SW OF RWY 34L",
              "TEMP CRANE 331 MSL, 3646FT SE OF RWY 24L",
              "TEMPORARY CRANE 2.2NM FROM DER, ON CENTERLINE, 300FT AGL",
              "TEMPORARY CRANES, UP TO 846 MSL, 1808FT SE OF RWY 10R",
              "BELOTTI CRANE 20 TONS UNSERVICEABLE",
              "NR 1 BELOTTI CRANE 36 TONS REF MILAIP",
              "CRANE MARKED, LGTD, 472413N0083627E, 95.0M",
              "TOWER CRANE ERECTED 250FT AGL", "MOBILE CRANE IN USE ADJ TWY B",
              "CRANE 15FT AGL WI 0.5NM", "CRANE 50 MSL", "CRANE OPR WI 0.5NM OF RWY 19 THR",
              "CRANEBROOK", "CRANE"):
        assert aim_parser._is_vip_notam(t) is False, t


# ── TFR authority priority (2026-10-05) ───────────────────────────────────────

import pytest as _pytest


@_pytest.fixture(autouse=True)
def _zones(monkeypatch):
    """Zones are per-deployment (no built-in default); tests pin a DC-style one."""
    monkeypatch.setenv("NOTAM_HOME_ARTCCS", "ZDC")
    monkeypatch.delenv("NOTAM_MONITOR_ARTCCS", raising=False)

def _n(fir, text):
    return {"fir": fir, "text_body": text}


def test_tfr_priority_everywhere_five():
    for t in ("PURSUANT TO 14 CFR SECTION 91.141", "PURSUANT TO 14 CFR SECTION 91.143",
              "SPECIAL SECURITY INSTRUCTIONS 14 CFR 99.7",
              "PURSUANT TO 49 USC 40103(B)(3) ... NATIONAL DEFENSE AIRSPACE"):
        assert aim_parser.tfr_priority(_n("ZFW", t)) == 5, t
        assert aim_parser.tfr_priority(_n("ZDC", t)) == 5, t


def test_tfr_priority_137_and_145_depend_on_the_home_zone():
    assert aim_parser.tfr_priority(_n("ZDC", "HURRICANE RELIEF 14 CFR SECTION 91.137(A)(2)")) == 5
    assert aim_parser.tfr_priority(_n("ZFW", "WILDFIRE 14 CFR SECTION 91.137(A)(2)")) == 3
    assert aim_parser.tfr_priority(_n("ZDC", "AIRSHOW 14 CFR SECTION 91.145")) == 5
    assert aim_parser.tfr_priority(_n("ZFW", "MIDLAND AIRSHOW 14 CFR SECTION 91.145")) == 3
    assert aim_parser.tfr_priority({"facility": "KZDC", "text_body": "14 CFR 91.137"}) == 5   # K-prefixed ARTCC


def test_tfr_priority_takes_the_highest_authority_and_ignores_others():
    assert aim_parser.tfr_priority(_n("ZFW", "91.145 AND 91.141")) == 5
    assert aim_parser.tfr_priority(_n("ZFW", "RWY 17 CLSD")) is None
    assert aim_parser.tfr_priority(_n("ZFW", "MIN ALT 991.1375")) is None


def test_home_zone_is_configurable(monkeypatch):
    monkeypatch.setenv("NOTAM_HOME_ARTCCS", "ZLA, ZOA")
    assert aim_parser.tfr_priority(_n("ZLA", "14 CFR 91.145")) == 5
    assert aim_parser.tfr_priority(_n("ZDC", "14 CFR 91.145")) == 3


def test_monitor_zones_sit_between_home_and_elsewhere(monkeypatch):
    monkeypatch.setenv("NOTAM_HOME_ARTCCS", "ZDC")
    monkeypatch.setenv("NOTAM_MONITOR_ARTCCS", "ZNY,ZTL,ZLA,ZOB")
    for fir, want in (("ZDC", 5), ("ZNY", 4), ("ZLA", 4), ("ZOB", 4), ("ZTL", 4), ("ZFW", 3)):
        assert aim_parser.tfr_priority(_n(fir, "14 CFR 91.137")) == want, fir
        assert aim_parser.tfr_priority(_n(fir, "14 CFR 91.145")) == want, fir
        assert aim_parser.tfr_priority(_n(fir, "14 CFR 91.141")) == 5, fir


def test_no_monitor_zones_by_default(monkeypatch):
    monkeypatch.delenv("NOTAM_MONITOR_ARTCCS", raising=False)
    assert aim_parser.tfr_priority(_n("ZNY", "14 CFR 91.145")) == 3


def test_zones_have_no_built_in_default(monkeypatch):
    monkeypatch.delenv("NOTAM_HOME_ARTCCS", raising=False)
    monkeypatch.delenv("NOTAM_MONITOR_ARTCCS", raising=False)
    assert aim_parser.tfr_priority(_n("ZDC", "14 CFR 91.145")) == 3
    assert aim_parser.tfr_priority(_n("ZDC", "14 CFR 91.141")) == 5


def test_public_edition_ships_the_zones_empty():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    ex = (root / "config/dispatch.env.example").read_text()
    assert "\nNOTAM_HOME_ARTCCS=\n" in ex and "\nNOTAM_MONITOR_ARTCCS=\n" in ex
    scrub = (root / "scripts/scrub-public-tree.py").read_text()
    local = (root / "config/dispatch.env").read_text()
    for line in ("NOTAM_HOME_ARTCCS=ZDC", "NOTAM_MONITOR_ARTCCS=ZNY,ZID,ZOB,ZLA,ZTL"):
        assert line + "\n" in local
        assert f'b"{line}\\n"' in scrub


# ── CRANE callsigns in live tracking (2026-10-05) ─────────────────────────────

def test_fdps_tracks_crane_callsigns_only():
    from ingest.parsers import fdps_parser as f
    for cs in ("CRANE01", "crane05", "CRANE50"):
        assert f.is_vip_callsign(cs) and f.is_marine_one(cs, None), cs
    for cs in ("CRANE", "CRANE1", "CRANE123", "CRANES", "XCRANE01", "AAL123"):
        assert not f.is_vip_callsign(cs), cs
    assert f.is_vip_callsign("SAM") and f.is_marine_one(None, "7700")
