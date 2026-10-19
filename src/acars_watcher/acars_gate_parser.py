"""
common/acars_gate_parser.py -- deterministic ACARS gate / baggage-carousel parser
(2026-10-18, gate batch; moved from the research prototype, vault designs/20261018T180905Z).

Pure functions, stdlib only. Every format is anchored on its own fixed structure
(field order + literal tokens). A message that merely contains GATE / BAG / BELT /
STAND / CAROUSEL never yields an assignment: it falls through to classify_noise()
and is labelled, not parsed.

parse(text) -> list of hits (the first, most specific matching format only):
    {fmt, kind, gate?, terminal?, bag?, stand?, flt?, day?, date?, al?, org?, dst?,
     station?, sent?, chg?, eta_local?, ...}
  kind:  ARR_GATE      announcement from ops (uplink)
         GATE_CHANGE   a change of an earlier announcement
         ARR_GATE_ACK  crew echo of an announcement (PSA 2-character ack) -- signal only
         GATE_REQ      crew / FMS request -- signal only
         STAND         international stand + belt formats (outside DC scope)

Binding a hit to a dated flight leg (flight id, date, destination checks) and the
confidence grade are common.gate_assignments.bind_acars()'s job, not this module's.

Operator decisions 2026-10-18 encoded here:
  - CONFIRMED_FORMATS: the formats the study proved carry a leg key in the text
    (UA GATE ASSIGN/CHANGE with header, AA arrival info with ORG-DST, RPA ARRIVAL
    GATE INFORMATION, Jazz gate data). Only these can be graded 'confirmed'.
  - PROVEN_CAROUSEL_FORMATS: the only formats whose BAG field counts as a baggage
    carousel (JetBlue CAROUSEL:, AA BAG n, LH group BAG BELT).
  - requests and the PSA ack are signals only (SIGNAL_KINDS).
"""
from __future__ import annotations

import re

PARSER_VERSION = "acars-gate-1"

G = r'(?P<gate>[A-Z]{0,2}\d{1,3}[A-Z]?)'          # B12, 14, D43A, T15, 23A, E43
_W = re.compile(r'\s+')

SIGNAL_KINDS = frozenset({"GATE_REQ", "ARR_GATE_ACK"})
ASSIGNMENT_KINDS = frozenset({"ARR_GATE", "GATE_CHANGE"})

CONFIRMED_FORMATS = frozenset({"AA_ARRINFO", "UA_GATE_ASSIGN", "YX_ARR_GATE_INFO", "QK_PLANNED_ARR_GATE"})
PROVEN_CAROUSEL_FORMATS = frozenset({"GATE_CAROUSEL_TURN", "AA_ARRINFO", "AA_ARR_BAG", "LHG_PAXINFO"})

# Carrier implied by a format whose text names no carrier code of its own (IATA).
FORMAT_CARRIER = {
    "AA_ARRINFO": "AA", "AA_REGIONAL_ARRINFO": "AA", "AA_NEW_ARR_GATE": "AA",
    "AA_ARR_BAG": "AA", "QK_PLANNED_ARR_GATE": "QK", "HA_ARR_GATE_MSG": "HA",
    "G4_INRANGE_GATE": "G4",
}


def _norm(t: str | None) -> str:
    return _W.sub(' ', (t or '').upper()).strip()


FORMATS = [
    # 1. American mainline arrival info (uplink). Header: FLT n/dd ORG-DST
    #    [BAG c] GATE g <ground-service block>. Gate and BAG (claim carousel) at DST.
    ('AA_ARRINFO', 'ARR_GATE', re.compile(
        r'(?:(?:FLT|X{2,8}) (?P<flt>\d{1,4}|X{2,8})(?:/(?P<day>\d{2}|XX))? |X{4,8} )?'
        r'(?P<org>[A-Z]{3})-(?P<dst>[A-Z]{3})'
        r'(?: BAG (?P<bag>[A-Z0-9]{1,3}))? GATE ' + G +
        r' ?(?=PCA|PAC|AIR |NO PAX|CREW CONNECTING)')),
    # 1b. Same family without the FLT header (continuation / short form).
    ('AA_ARRINFO_NOHDR', 'ARR_GATE', re.compile(
        r'^GATE ' + G + r' (?=PCA [YN]|PAC [YN]|AIR [YN] PWR|NO PAX CNX)')),
    # 2. American international/widebody variant: ARR g BAG c FLT ETD DEST ...
    ('AA_ARR_BAG', 'ARR_GATE', re.compile(
        r'(?:^|\s)ARR ' + G + r' BAG(?: (?P<bag>[A-Z0-9]{1,3}))? FLT ETD DEST')),
    # 3. American regional (Envoy / PSA / Piedmont / Republic-for-AA). The route
    #    meaning varies, so the gate airport comes from the leg (bind_acars).
    ('AA_REGIONAL_ARRINFO', 'ARR_GATE', re.compile(
        r'(?:FLT (?P<flt>\S+) (?P<org>[A-Z]{3})-(?P<dst>[A-Z]{3}) )?GATE ' + G +
        r' ETA (?P<eta_local>\d{4})L (?:TURN FLT (?P<turn_flt>\S+) (?P<station>[A-Z]{3}) DEP (?P<turn_dep>\d{4})L|TERMINATING FLIGHT)')),
    # 4. United GATE ASSIGN / GATE CHANGE, full header form.
    ('UA_GATE_ASSIGN', 'ARR_GATE', re.compile(
        r'GATE (?P<chg>ASSIGN|CHANGE) (?P<al>[A-Z0-9]{2})(?P<flt>\d{1,4}|X{2,6})/(?P<day>\d{2}) '
        r'(?P<org>[A-Z]{4}) (?P<dst>[A-Z]{4}) SENT: (?P<sent>\d{2}:\d{2}:\d{2})Z'
        r'.*?\bGATE ' + G + r' (?:AVAIL (?P<avail_local>\d{4})|OPEN|CONTACT OPS)')),
    # 4b. United body without the header (Jumpseat splits long uplinks into parts).
    ('UA_GATE_ASSIGN_NOHDR', 'ARR_GATE', re.compile(
        r'GATE ASSIGN(?: \*\*\*[^*]+\*\*\*)? GATE ' + G + r' (?:AVAIL (?P<avail_local>\d{4})|OPEN|CONTACT OPS)')),
    # 5. Lufthansa Group PAX INFO: ... ARR TERM t ARR GATE g ARR POS p BAG BELT b
    ('LHG_PAXINFO', 'ARR_GATE', re.compile(
        r'PAX INFO STA/ETA (?P<eta_local>\d{4})LT ARR TERM (?P<terminal>.{1,12}?) ARR GATE (?P<gate>\S+) '
        r'ARR POS (?P<stand>\S+)(?: BAG BELT (?P<bag>\S+))?')),
    # 6. SkyWest arrival gate: '<STA> ARRIVAL GATE g'. Crew names follow NEXT FLIGHT
    #    INFORMATION -- nothing after the gate is kept.
    ('SKW_ARRIVAL_GATE', 'ARR_GATE', re.compile(
        r'(?:^|\s)(?P<station>[A-Z]{3}) ARRIVAL GATE ' + G + r'(?=\s|$)')),
    # 7. KLM gate/stand change: GATE/POS AT AMS CHANGED TO E08/E08
    ('KL_GATEPOS_CHANGE', 'GATE_CHANGE', re.compile(
        r'GATE/POS AT (?P<station>[A-Z]{3}) CHANGED TO (?P<gate>\w+)/(?P<stand>\w+)')),
    # 8. JetBlue: GATE:x CAROUSEL:n AC TURN:
    ('GATE_CAROUSEL_TURN', 'ARR_GATE', re.compile(
        r'GATE:(?P<gate>[A-Z]?\d{1,3}[A-Z]?) CAROUSEL:(?P<bag>\w{1,3}) AC TURN')),
    # 9. Australian ops
    ('AU_ARRIVAL_GATE', 'ARR_GATE', re.compile(r'ARRIVAL GATE THIS FLT ?: ?(?P<gate>\w+)')),
    ('AU_PARKBAY_CAROUSEL', 'STAND', re.compile(
        r'PARKBAY - (?P<stand>\w+) BAGGAGECAROUSEL - (?P<bag>\w+)')),
    # 10. Swiss / LHG connex trailer: ... - ARRIVAL GATE: A17
    ('LHG_CONNEX_ARRGATE', 'ARR_GATE', re.compile(r'- ARRIVAL GATE: (?P<gate>[A-Z]?\d{1,3}[A-Z]?)\b')),
    # 11. Frontier-style ops ETA uplink
    ('F9_OPS_GATE', 'ARR_GATE', re.compile(r'\bGATE ' + G + r' GATE AVAIL AT ARRIVAL (?P<avail>[YN])')),
    # 11b. American gate change: NEW ARRIVAL GATE NBR IS 35
    ('AA_NEW_ARR_GATE', 'GATE_CHANGE', re.compile(r'NEW ARRIVAL GATE NBR IS ' + G + r'\b')),
    # 12. PSA (OH) downlink ack of the gate uplink (2-character truncated field)
    ('OH_ACKFLT_GATE', 'ARR_GATE_ACK', re.compile(r'^/ACKFLT GATE ' + G + r'$')),
    # 13. United downlink gate request
    ('UA_GATE_REQ', 'GATE_REQ', re.compile(
        r'^/C3 GATE REQ\s*/ (?P<org>[A-Z]{4}) (?P<dst>[A-Z]{4}) (?P<day>\d{2}) \S+ (?P<flt>\w{3,4})')),
    # 14. EI stand enquiry (request)
    ('EI_STAND_ENQ', 'GATE_REQ', re.compile(r'STAND ENQUIRY\. ?PLEASE SEND STAND INFO')),
    # 15. Republic (YX) arrival gate
    ('YX_ARR_GATE_INFO', 'ARR_GATE', re.compile(
        r'ARRIVAL GATE INFORMATION RPA (?:\S+ )?(?P<al>YX)(?P<flt>\d{1,4}) (?P<org>[A-Z]{3}) (?P<dst>[A-Z]{3}) '
        r'ETA = (?P<eta_local>\d{4}) GATE = (?P<gate>[A-Z]?\d{1,3}[A-Z]?)')),
    # 16. Allegiant in-range notice
    ('G4_INRANGE_GATE', 'ARR_GATE', re.compile(
        r'FLT (?P<flt>\d{1,4})\*+IN-RANGE NOTICE\*+ --GATE ASSIGNMENT (?P<station>[A-Z]{3}) GATE (?P<gate>[A-Z]?\d{1,3}[A-Z]?) ASSIGNED')),
    # 17. Jazz (QK) gate data uplink: full date + route
    ('QK_PLANNED_ARR_GATE', 'ARR_GATE', re.compile(
        r'FLIGHT: (?P<flt>\d{1,4})/(?P<date>\d{2}[A-Z]{3}\d{2})/(?P<org>[A-Z]{3})/(?P<dst>[A-Z]{3}) PLANNED ARRIVAL GATE IS (?P<gate>[A-Z]?\d{1,3}[A-Z]?)')),
    # 18. Hawaiian
    ('HA_ARR_GATE_MSG', 'ARR_GATE', re.compile(
        r'ARR GATE MSG (?P<flt>\d{1,4}) ARR GATE (?P<gate>[A-Z]?\d{1,3}[A-Z]?) GND PWR')),
    # 19. Alaska gate change / ETA ack
    ('AS_GATE_CHANGE', 'GATE_CHANGE', re.compile(
        r'GATE CHANGE IN (?P<station>[A-Z]{3}) (?P<al>AS)(?P<flt>\d{1,4}) NEW GATE: (?P<gate>[A-Z]?\d{1,3}[A-Z]?)')),
    ('WS_GATE_CHANGE', 'GATE_CHANGE', re.compile(
        r'GATECHANGE/+ (?P<al>WS) (?P<flt>\d{1,4}) (?P<org>[A-Z]{3})-(?P<dst>[A-Z]{3}) ARR GATE IS NOW (?P<gate>[A-Z]?\d{1,3}[A-Z]?)')),
    ('AS_ETA_ACK_GATE', 'ARR_GATE', re.compile(
        r'ETA ACK FROM:(?P<station>[A-Z]{3}) OPS (?P<al>AS)(?P<flt>\d{1,4}) ETA:(?P<eta_local>\d{4}) ARRIVAL GATE:(?P<gate>[A-Z]?\d{1,3}[A-Z]?)')),
    # 21. Downlink request, flight-id prefix CM
    ('CM_GATE_REQ', 'GATE_REQ', re.compile(
        r'^(?P<day>\d{2})(?P<dst>[A-Z]{4}) REQUEST GATE ASSIGNMENT ETA ?(?P<eta>\d{4})')),
    # 22. International stand + baggage belt (observed; outside the DC scope)
    ('CZ_BELT_STAND', 'STAND', re.compile(r'BAGGAGE CLAIM BELT: (?P<bag>\w{1,3}) STAND NO: (?P<stand>\w+)')),
    ('CX_BAY_CAROUSEL', 'STAND', re.compile(r'BAY (?:GATE )?(?P<stand>\w+) BAGGAGE CAROUSEL (?P<bag>\w{1,3})')),
    ('HKG_BAY_BELT', 'STAND', re.compile(r'PARKING BAY (?P<stand>\w+) BAG BELT (?P<bag>\w{1,3})')),
    ('QF_CAROUSEL', 'STAND', re.compile(r'BAGGAGE ?CAROUSELL?(?: -|:) ?(?P<bag>\d{1,2})\b')),
]

# Contain the keywords but are NOT assignments (labelled only).
NOISE = [
    ('ATIS_NOTAM', re.compile(r'ADZ GATE ASSIGNMENT|GATE ASSIGNMENT TO APPROACH|NOTICE TO AIRMEN|TWY \w+ CLSD BTN GATES|ATIS|QNH|INFO [A-Z] ')),
    ('CREW_CONNECTION_ONLY', re.compile(r'^CREW CONNECTING GATE INFO')),
    ('DEPARTURE_CONNECTIONS', re.compile(r'FLN TO DEP PAX GATE|TRANSFER LIST|CONNEX|CONNECTING GATES')),
    ('STATUS_AT_GATE', re.compile(r'\b(?:AT|ON|TO|FROM|INTO) (?:THE )?GATE\b|GATE HOLD|RETURN TO (?:THE )?GATE')),
    ('LOADSHEET', re.compile(r'LOADSHEET|LOAD SHEET|LIR ED|ZFW|TOW ')),
    ('SEATBELT', re.compile(r'SEAT ?BELT')),
    ('MAINT_GATEWAY', re.compile(r'GATEWAY')),
    ('PARKING_BRAKE', re.compile(r'PARKING BRAKE|PARK BRAKE')),
    ('DELAY_CODES', re.compile(r'P-CODE|DLY REASON|PCGO-CARGO')),
]

# Cheap pre-filter: a message without one of these tokens cannot match any format.
_CANDIDATE_RE = re.compile(r'GATE|CAROUSEL|BAG BELT|BAGGAGE|STAND|PARKBAY|PARKING BAY|BAY ')


def is_candidate(text: str | None) -> bool:
    return bool(text) and bool(_CANDIDATE_RE.search((text or '').upper()))


def parse(text: str | None) -> list[dict]:
    """Hits of the first (most specific) matching format, in text order. [] if none."""
    t = _norm(text)
    if not t:
        return []
    out: list[dict] = []
    for name, kind, rx in FORMATS:
        for m in rx.finditer(t):
            d = {k: v for k, v in m.groupdict().items() if v}
            d['fmt'] = name
            d['kind'] = kind
            if name == 'UA_GATE_ASSIGN' and d.get('chg') == 'CHANGE':
                d['kind'] = 'GATE_CHANGE'
            out.append(d)
        if out:
            break
    return out


def format_grade(hit: dict) -> str:
    """The best grade the TEXT alone allows: 'request' (signal only), 'ack' (signal
    only), 'confirmed' (a CONFIRMED_FORMATS header with a leg key in the text) or
    'probable'. bind_acars() can only lower it (direction, flight, date, destination)."""
    if hit.get('kind') == 'GATE_REQ':
        return 'request'
    if hit.get('kind') == 'ARR_GATE_ACK':
        return 'ack'
    has_leg_key = bool(hit.get('flt') and (hit.get('day') or hit.get('date'))) or \
        bool(hit.get('org') and hit.get('dst'))
    if hit.get('fmt') in CONFIRMED_FORMATS and has_leg_key:
        return 'confirmed'
    return 'probable'


def classify_noise(text: str | None) -> str:
    t = _norm(text)
    for name, rx in NOISE:
        if rx.search(t):
            return name
    return 'OTHER_FREE_TEXT'


def masked(value: str | None) -> bool:
    """ACARS Drama / airframes mask some ids as X runs."""
    return bool(value) and set(value) <= {'X'}


def baggage_counts(hit: dict) -> bool:
    """A hit's BAG field is a baggage carousel only for the proven formats."""
    return hit.get('fmt') in PROVEN_CAROUSEL_FORMATS and bool(hit.get('bag')) and not masked(hit.get('bag'))
