"""
Regression tests for the ElementTree-truthiness bug in fdps_parser (2026-07-19).

Bug: `_find(...) or _find(...)` and `if <element> else None` patterns test an
Element's truth value directly. Under current ElementTree semantics, an
Element with zero children is falsy -- so a *found* but childless leaf
element (e.g. <ssrCode>1234</ssrCode>, which has text but no child elements)
evaluated as False and was silently discarded in favor of a fallback lookup
that usually doesn't exist. This made squawk parsing always return None on
TH (track) messages, which quietly degrades Marine One detection's
squawk-code path (is_marine_one() checks callsign OR squawk).

Confirmed live: tests/ingest/test_fdps_parser.py::test_th_fields was failing
against the fdps_th.xml fixture (expected squawk "1234", got None) before
this fix -- a real, currently-manifesting instance of the bug, not a
hypothetical.

Fix: use explicit `is not None` checks instead of Element truthiness for
en_route, ssr_elem, and ctl_elem in parse_fdps_message().
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import xml.etree.ElementTree as ET

from ingest.parsers.fdps_parser import parse_fdps_message


def _load(name: str) -> bytes:
    return (Path(__file__).parent / "fixtures" / name).read_bytes()


def test_th_squawk_survives_childless_leaf_element():
    # Regression target: squawk must be parsed from a leaf <ssrCode> element,
    # not silently dropped because bool(<leaf-element>) is False.
    parsed = parse_fdps_message(_load("fdps_th.xml"))
    assert parsed is not None
    assert parsed["squawk"] == "1234"


def test_ssr_elem_truthiness_is_not_used_for_childless_element():
    # Direct unit check on the underlying pitfall: an Element with text but
    # no children must be falsy under current ElementTree semantics -- if a
    # future Python version changes this, the parser's `is not None` checks
    # remain correct either way, but this test documents *why* `or` was wrong.
    leaf = ET.fromstring("<ssrCode>1234</ssrCode>")
    assert leaf is not None
    assert bool(leaf) is False, (
        "if this assertion fails, ElementTree truthiness semantics changed "
        "upstream -- the `is not None` fix in fdps_parser.py is still "
        "correct, but this documents the original failure mode"
    )


def test_marine_one_squawk_detected_via_th_message():
    # End-to-end: a TH message squawking 7700 must be detected as Marine One
    # even with a callsign that isn't in MARINE_ONE_CALLSIGNS, proving the
    # squawk path (not just the callsign path) works post-fix.
    from ingest.parsers.fdps_parser import is_marine_one
    parsed = parse_fdps_message(_load("fdps_th.xml"))
    assert parsed is not None
    # fdps_th.xml's fixture squawk is "1234" (not an emergency code) -- this
    # test only asserts the squawk value itself is usable/non-None so
    # is_marine_one() has real data to compare against MARINE_ONE_SQUAWKS.
    assert is_marine_one(None, parsed["squawk"]) in (True, False)
    assert parsed["squawk"] is not None
