"""Regression tests for the 2026-08-16 drift-audit TFMS '+?min' fix.

Live-confirmed: MIT/MINIT/APREQ/STOP restriction alerts rendered
"avg delay +?min" because _handle_restriction never sets avg_delay_minutes
(a MIT is miles-in-trail spacing, not a delay). Field shapes below are
copied verbatim from real nas_programs.raw_json rows on this box.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from ingest.parsers.tfms_parser import _tfms_program_metric


def test_mit_shows_miles_in_trail_not_delay():
    # Real row: MIT LGA, mit_value="30", no avg_delay_minutes key.
    prog = {"type": "MIT", "facility": "LGA", "mit_value": "30",
            "reason": "TM Initiatives:MIT:WX", "restriction_type": "ENROUTE"}
    assert _tfms_program_metric(prog) == "30 NM in-trail"
    assert "?" not in _tfms_program_metric(prog)


def test_minit_shows_minutes_in_trail():
    prog = {"type": "MINIT", "facility": "N90", "mit_value": "5",
            "reason": "VOL:Volume"}
    assert _tfms_program_metric(prog) == "5 min in-trail"


def test_apreq_with_no_value_has_no_fake_metric():
    # Real row: APREQ JFK, mit_value=null -> no numeric metric at all.
    prog = {"type": "APREQ", "facility": "JFK", "mit_value": None,
            "reason": "VOL:Volume"}
    assert _tfms_program_metric(prog) == ""


def test_ground_stop_has_no_metric():
    prog = {"type": "STOP", "facility": "IAD", "reason": "WX:Thunderstorms"}
    assert _tfms_program_metric(prog) == ""


def test_gdp_delay_program_unchanged():
    # GDP/GS DO carry avg_delay_minutes -- the one case the old code got right.
    prog = {"type": "GDP", "facility": "EWR", "avg_delay_minutes": "45",
            "reason": "WX", "mit_value": None}
    assert _tfms_program_metric(prog) == "avg delay +45min"


def test_avg_delay_takes_precedence_over_mit_value():
    # Defensive: if both somehow present, the delay figure wins (GDP semantics).
    prog = {"type": "GDP", "avg_delay_minutes": "20", "mit_value": "10"}
    assert _tfms_program_metric(prog) == "avg delay +20min"


def test_none_string_is_treated_as_missing():
    prog = {"type": "MIT", "facility": "DCA", "mit_value": "None",
            "avg_delay_minutes": None, "reason": "x"}
    assert _tfms_program_metric(prog) == ""
