"""2026-10-18 (gate batch): the deterministic ACARS gate / baggage parser
(src/common/acars_gate_parser.py), moved from the research prototype with its
31 synthetic fixtures (vault designs/20261018T180905Z). Synthetic text only: no
real flight, tail, hex, name or phone.

Each fixture: (text, direction, expected fmt or None, expected fields, expected
text-only grade). 'confirmed' is only the best grade the TEXT allows; binding to
a leg (common.gate_assignments.bind_acars) can only lower it."""
from __future__ import annotations

from pathlib import Path

import pytest

from common import acars_gate_parser as agp

ROOT = Path(__file__).resolve().parents[2]

FIXTURES = [
    # American mainline, header + BAG (claim carousel) at DST
    ("FLT 1234/17 BOS-DCA BAG 5 GATE C35 PCA Y ELEC Y DGS Y CREW CONNECTING GATE INFO CREW SEAT FLT DEST GATE TIME NO CREW CNX END",
     'up', 'AA_ARRINFO', {'flt': '1234', 'day': '17', 'org': 'BOS', 'dst': 'DCA', 'bag': '5', 'gate': 'C35'}, 'confirmed'),
    ("XXXX XXXX/XX ORD-DCA GATE B14 PCA N ELEC Y DGS N REMARKS TEST", 'up', 'AA_ARRINFO', {'org': 'ORD', 'dst': 'DCA', 'gate': 'B14'}, 'confirmed'),
    ("GATE D7 PCA Y ELEC Y NO PAX CNX", 'up', 'AA_ARRINFO_NOHDR', {'gate': 'D7'}, 'probable'),
    ("ARR B12 BAG 7 FLT ETD DEST NO CONNECTING PAX", 'up', 'AA_ARR_BAG', {'gate': 'B12', 'bag': '7'}, 'probable'),
    ("ARR 45 BAG FLT ETD DEST NO CONNECTING PAX", 'up', 'AA_ARR_BAG', {'gate': '45'}, 'probable'),
    # American regional, both route semantics
    ("FLT 4999 DCA-BGR GATE 4 ETA 1223L TURN FLT 4999 DCA DEP 1306L FREQ-130.05 PWR-Y", 'up', 'AA_REGIONAL_ARRINFO',
     {'org': 'DCA', 'dst': 'BGR', 'gate': '4', 'eta_local': '1223', 'station': 'DCA'}, 'probable'),
    ("FLT 5999 ROC-DCA GATE E52 ETA 1839L TURN FLT 5998 CHA DEP 2100L ALL GATES UNDER KDCA GRND CNTRL", 'up', 'AA_REGIONAL_ARRINFO',
     {'org': 'ROC', 'dst': 'DCA', 'gate': 'E52', 'station': 'CHA'}, 'probable'),
    ("GATE A12 ETA 1048L TERMINATING FLIGHT LAV SVC PLZ CALL IN RANGE", 'up', 'AA_REGIONAL_ARRINFO', {'gate': 'A12', 'eta_local': '1048'}, 'probable'),
    ("NEW ARRIVAL GATE NBR IS 35", 'up', 'AA_NEW_ARR_GATE', {'gate': '35'}, 'probable'),
    # United
    ("QUNDCULUA~1GATE ASSIGN UA0999/17 KORD KIAD SENT: 14:23:53Z ***ACCOMPLISH TERMINATION CHECKLIST*** GATE C9 AVAIL 1544 RAMP FREQ 129.075 OPS FREQ 130.500 EON 1941Z GROUND PWR AVAIL -YES- NEXT FLT UA0998/17 KIAD KDEN ETD 2120Z",
     'up', 'UA_GATE_ASSIGN', {'al': 'UA', 'flt': '0999', 'day': '17', 'org': 'KORD', 'dst': 'KIAD', 'sent': '14:23:53', 'gate': 'C9', 'avail_local': '1544'}, 'confirmed'),
    ("QUNDCULUA~1GATE CHANGE UA0999/17 KDEN KIAD SENT: 18:44:24Z ///// **** CREW ALERT **** GATE ASSIGN GATE D30 CONTACT OPS* RAMP FREQ 129.075",
     'up', 'UA_GATE_ASSIGN', {'chg': 'CHANGE', 'dst': 'KIAD', 'gate': 'D30'}, 'confirmed'),
    ("GATE ASSIGN GATE E6 AVAIL 1405 RAMP FREQ 131.000 OPS FREQ 129.100", 'up', 'UA_GATE_ASSIGN_NOHDR', {'gate': 'E6'}, 'probable'),
    ("/C3 GATE REQ       / KEWR KIAD 17 ABC123 0999 ---- ---- ---- ----", 'down', 'UA_GATE_REQ', {'org': 'KEWR', 'dst': 'KIAD', 'day': '17'}, 'request'),
    # Republic
    ("QUHDQDDRP~1ARRIVAL GATE INFORMATION RPA NXXXXX YX9999 BOS IAD ETA = 1540 GATE = A2 AIRCRAFT TURNS TO - YX9998 IAD-BOS ETD-1645 NOTE - NEXT FLT SUBJECT TO CHANGE",
     'up', 'YX_ARR_GATE_INFO', {'flt': '9999', 'org': 'BOS', 'dst': 'IAD', 'eta_local': '1540', 'gate': 'A2'}, 'confirmed'),
    # SkyWest / Alaska / Allegiant / JetBlue
    ("QUSILITOO.1ARR GATE INFO ORD ARRIVAL GATE F22 NEXT FLIGHT INFORMATION- ** ALL TIMES LOCAL **", 'up', 'SKW_ARRIVAL_GATE', {'station': 'ORD', 'gate': 'F22'}, 'probable'),
    (",, ETA ACK FROM:PDX OPS AS999 ETA:1637 ARRIVAL GATE:C18 EXT ELECT AVAIL", 'up', 'AS_ETA_ACK_GATE', {'station': 'PDX', 'flt': '999', 'gate': 'C18'}, 'probable'),
    ("*AUTOMATED MSG FLT 999******IN-RANGE NOTICE***** --GATE ASSIGNMENT SDF GATE A10 ASSIGNED --NEXT FLIGHT 998", 'up', 'G4_INRANGE_GATE', {'station': 'SDF', 'gate': 'A10'}, 'probable'),
    ("GATE:C23B CAROUSEL:8 AC TURN: FLL ON TIME 1555L", 'up', 'GATE_CAROUSEL_TURN', {'gate': 'C23B', 'bag': '8'}, 'probable'),
    # Lufthansa group with baggage belt
    ("PAX INFO STA/ETA 1658LT ARR TERM 2 ARR GATE E43 ARR POS E43 BAG BELT 22 CONNECTING GATE INFO", 'up', 'LHG_PAXINFO',
     {'eta_local': '1658', 'terminal': '2', 'gate': 'E43', 'stand': 'E43', 'bag': '22'}, 'probable'),
    # PSA ack (2-char truncated field -> never an assignment on its own)
    ("/ACKFLT GATE E5", 'down', 'OH_ACKFLT_GATE', {'gate': 'E5'}, 'probable'),
    # Negative cases: keyword present, no assignment
    ("TOWER FREQ 118.5 FOR ALL RUNWAYS. ADZ GATE ASSIGNMENT TO APPROACH CTL ON INITIAL CTC.", 'up', None, {}, None),
    ("NOTICE TO AIRMEN. TWY K CLSD BTN GATES D9 AND E1.", 'up', None, {}, None),
    ("PLZ HAVE MED PERSONNEL MEET US AT GATE", 'down', None, {}, None),
    ("CAN YOU CONFIRM GATE,D7. REPORT DONT SHOW A GATE.", 'down', None, {}, None),
    ("SEAT BELT ON 597 STATUS", 'down', None, {}, None),
    ("DB LEFT FORWARD AUDIO GATEWAY UNIT", 'down', None, {}, None),
    ("CREW CONNECTING GATE INFO CREW SEAT FLT DEST GATE TIME C F 0999DCA C24 1249 END", 'up', None, {}, None),
    ("ARR GATE BAG NO CONNECTING PAX", 'up', None, {}, None),
    ("WILL HEAD TO RIC IN A FEW MINS.. IF WE CAN WORK ON A GATE THAT WOULD BE GREAT", 'down', None, {}, None),
    ("NEW GATE 81 THANK YOU SO MUCH", 'down', None, {}, None),
    ("SKW ARRIVAL GATE NEXT FLIGHT INFORMATION- ** ALL TIMES LOCAL ** N-A 5449", 'up', None, {}, None),
]


@pytest.mark.parametrize("text,direction,fmt,fields,grade", FIXTURES)
def test_fixture(text, direction, fmt, fields, grade):
    hits = agp.parse(text)
    if fmt is None:
        assert hits == [], hits
        return
    assert hits, text
    h = hits[0]
    assert h["fmt"] == fmt
    for k, v in fields.items():
        assert h.get(k) == v, (k, h)
    want = "ack" if h["kind"] == "ARR_GATE_ACK" else grade
    assert agp.format_grade(h) == want


def test_fixture_count_matches_the_study():
    assert len(FIXTURES) == 31
    assert sum(1 for f in FIXTURES if f[2] is None) == 11


def test_watcher_copy_is_byte_identical():
    # src/acars_watcher is its own build context: the parser ships as a twin there.
    a = (ROOT / "src/common/acars_gate_parser.py").read_bytes()
    b = (ROOT / "src/acars_watcher/acars_gate_parser.py").read_bytes()
    assert a == b


def test_only_proven_formats_can_be_confirmed():
    assert agp.CONFIRMED_FORMATS == {"AA_ARRINFO", "UA_GATE_ASSIGN", "YX_ARR_GATE_INFO", "QK_PLANNED_ARR_GATE"}
    hit = agp.parse("QUSILITOO.1ARR GATE INFO ORD ARRIVAL GATE F22 NEXT FLIGHT INFORMATION-")[0]
    assert agp.format_grade(hit) == "probable"


def test_only_proven_carousel_formats_count_as_baggage():
    assert agp.baggage_counts(agp.parse("GATE:C23B CAROUSEL:8 AC TURN: FLL ON TIME 1555L")[0])
    assert agp.baggage_counts(agp.parse("ARR B12 BAG 7 FLT ETD DEST NO CONNECTING PAX")[0])
    cz = agp.parse("BAGGAGE CLAIM BELT: 34 STAND NO: 273")[0]
    assert cz["bag"] == "34" and not agp.baggage_counts(cz)      # observed, not proven


def test_ua_gate_change_is_a_change_kind():
    h = agp.parse("GATE CHANGE UA0999/17 KDEN KIAD SENT: 18:44:24Z ///// GATE ASSIGN GATE D30 CONTACT OPS*")[0]
    assert h["kind"] == "GATE_CHANGE" and h["gate"] == "D30"


def test_skywest_crew_names_after_the_gate_are_never_captured():
    h = agp.parse("ORD ARRIVAL GATE F22 NEXT FLIGHT INFORMATION- CAPT JOHN Q SAMPLE FO JANE R EXAMPLE")[0]
    assert set(h) <= {"fmt", "kind", "station", "gate"}


def test_candidate_prefilter():
    assert agp.is_candidate("... GATE C9 ...") and not agp.is_candidate("POS REPORT 3900N")
