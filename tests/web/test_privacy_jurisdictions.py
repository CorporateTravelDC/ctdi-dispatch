"""2026-11-02 (operator): non-US private aircraft follow their own country's privacy
default; opt-out requests are honoured in any country; a non-US owner name is shown only
where that country publishes it. Synthetic registrations and hexes only."""
from __future__ import annotations

import pytest

from common import privacy_jurisdictions as pj


@pytest.mark.parametrize("reg,hx,country", [
    ("D-AXYZ", None, "DE"), ("DAXYZ", None, "DE"), ("G-ABCD", None, "GB"), ("CS-DXX", None, "PT"),
    ("C-GABC", None, "CA"), ("CGABC", None, "CA"), ("A6-ABC", None, "AE"), ("9H-ABC", None, "MT"),
    ("HB-JAB", None, "CH"), ("VP-BXX", None, "??"), (None, "3c1234", "DE"), (None, "a00001", "US"),
    (None, "c01234", "CA"), (None, "896012", "AE"), (None, "ffffff", None), (None, None, None)])
def test_country_from_registration_then_hex(reg, hx, country):
    assert pj.country_of(reg, hx) == country


@pytest.mark.parametrize("cs,reg,private", [
    ("DAXYZ", "D-AXYZ", True), (None, "D-AXYZ", True), ("", None, True), ("DLH400", "D-AIXA", False),
    ("VJT123", "9H-VJA", False), ("EZY12AB", None, False), ("GABCD", "G-ABCD", True), ("BAW1", "G-EUPA", False)])
def test_private_means_no_operator_designator(cs, reg, private):
    assert pj.is_private(cs, reg) is private


def test_country_defaults():
    assert pj.withheld_by_country({"registration": "D-AXYZ", "callsign": "DAXYZ"})       # Germany private
    assert not pj.withheld_by_country({"registration": "D-AIXA", "callsign": "DLH400"})  # airline
    assert pj.withheld_by_country({"registration": "A6-ABC"})                             # UAE private
    assert not pj.withheld_by_country({"registration": "C-GABC", "callsign": "CGABC"})   # Canada shows
    assert not pj.withheld_by_country({"registration": "VP-BXX"})                          # no default
    assert not pj.withheld_by_country({"hex": "a00001", "callsign": "TST1"})               # US: LADD rules
    assert pj.withheld_by_country({"hex": "3c1234", "flight": "  "})                       # German block


def test_owner_names_follow_the_country():
    assert not pj.owner_public("D-AXYZ") and not pj.owner_public("A6-ABC") and not pj.owner_public("ZK-ABC")
    assert pj.owner_public("G-ABCD") and pj.owner_public("C-GABC")
    assert not pj.owner_public("VP-BXX")                                                  # unknown: hidden
    assert pj.owner_public(None, "a00001")                                                # US public register
