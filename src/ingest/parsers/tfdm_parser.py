"""
ingest.parsers.tfdm_parser -- SWIM TFDM Terminal Publication (TTP), 2026-11-18.

Why this exists: the operator's SCDS subscription "TFDM" (TTP Airport
Information, Traffic Management Restrictions, Flight Delay and Flight Data, all
message types and aerodromes) was provisioned but nothing ever connected to it.
On 2026-11-18 the FAA wrote that it will be disabled for lack of connectivity
within 30 days, and removed 30 days after that. The first job of this feed is
therefore to stay connected and drain the queue; parsing is secondary and can
never stop a message from being acknowledged (swim_client acks in a finally).

What is read today, and only this:
  - Flight Data: the DEPARTURE stand (gate) at the departure aerodrome, as SWIM
    departure-gate evidence (common.gate_assignments source 'swim_tfdm', tier 40,
    the same tier as TDES). In the US "gate" and "stand" mean the same thing
    (FIXM US Extension data dictionary). Gate/stand only: no time, no status, no
    OOOI phase and no tail is taken from this feed (docs/glossary.yaml logic rules:
    a tail comes only from FDPS/TDES/TBFM/TFMS; OOOI only from the SWIM sources the
    OOOI authority table names, and TFDM is not one of them yet).
    SUPERSEDED 2026-11-19 for the tail (operator): TFDM's aircraft registration now binds a
    watched leg's tail BEFORE departure, ahead of SFDPS (common.tfdm_authority). Still no
    time, status or OOOI from this feed.
  - Everything else (Airport Information, Traffic Management Restrictions, Flight
    Delay, arrival stands, Heartbeat and the periodic resync markers) is COUNTED
    by data group and message type, nothing more. Future work, not guessed at.

Schema evidence (element names are used only where a public document shows them):
  NASA ATD-2 "TTP Flight Data Messages" (NTRS 20180007226; ATD-2 runs the TFDM
  schema as a prototype): JMS headers DATA_GROUP, MESSAGE_TYPE, SYNC, AERODROME,
  SCHEMA_VERSION; the body is a FIXM 4.x NAS extension document
      <nas:NasMessage> <nas:flight> <fx:departure departurePointText="KCLT">
        <nas:standInformation standName="B12"/> ... </fx:departure>
      <fx:destination destinationPointText="KSAN"> ... </fx:destination>
      <nas:flightIdentificationPrevious aircraftIdentification="AAL470"/> or
      flightIdentification/@aircraftIdentification;
      additionalFlightInformation/nameValue name="TFDMID"; metadata/@messageType.
  The production TFDM schema may differ in namespace version, so every lookup
  here is by LOCAL NAME, namespace-agnostic, and a shape it does not recognise
  is counted, never raised. The test fixtures are synthetic and say so.
"""
from __future__ import annotations

import logging
import threading
import time
import xml.etree.ElementTree as ET

log = logging.getLogger("ingest.parsers.tfdm")

PARSER_VERSION = "tfdm-ttp-3"   # 2026-11-20: paths checked against live samples; runway times, tfdmId, flight/ATC state

# Departure aerodromes whose stands are recorded without a watch match: the same
# DC set the TDES path uses (smes_parser.SMES_AIRPORTS). Any other aerodrome's
# stand is recorded only when the flight matches a watch.
DC_AIRPORTS = frozenset({"KDCA", "KIAD", "KBWI"})

# Data groups the subscription carries (SCDS service names -> header DATA_GROUP).
KNOWN_DATA_GROUPS = ("FlightData", "FlightDelay", "TrafficManagementRestrictions",
                     "AirportInformation")

_COUNTS_LOCK = threading.Lock()
_COUNTS: dict[tuple[str, str], int] = {}
# Summary cadence: every 60 s for the first 10 min after start (so a fresh
# connection shows its counts quickly), then every 5 min. The first message
# ever is summarised at once.
_SUMMARY_EVERY_S = 300.0
_SUMMARY_EARLY_S = 60.0
_EARLY_WINDOW_S = 600.0
_started = time.monotonic()
_last_summary = _started


def _local(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _find(el, *names):
    """First descendant whose local name is the first name, then its child the
    second, and so on. Namespace-agnostic."""
    cur = el
    for name in names:
        nxt = None
        for child in cur.iter():
            if child is not cur and _local(child.tag) == name:
                nxt = child
                break
        if nxt is None:
            return None
        cur = nxt
    return cur


def _children(el, name):
    return [c for c in list(el) if _local(c.tag) == name]


def _attr(el, name):
    if el is None:
        return None
    for k, v in el.attrib.items():
        if _local(k) == name:
            v = (v or "").strip()
            return v or None
    return None


def _text(el):
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def _when(el):
    """A time value that is either the element's own text (offBlockTime/initial) or its child
    <time> (runwayDepartureTime/estimated, .../actual, .../earliest), as the live feed sends them
    (2026-11-20 samples; the parser used to read only the element's own text, so every runway
    time came back empty)."""
    if el is None:
        return None
    return _text(el) or _text(_find(el, "time"))


def _guess_data_group(root, headers: dict | None) -> str:
    dg = (headers or {}).get("DATA_GROUP")
    if dg:
        return str(dg)
    name = _local(root.tag) if root is not None else ""
    if _find(root, "flight") is not None:
        return "FlightData"
    return name or "unknown"


def count(data_group: str, message_type: str) -> None:
    """Count one message; log a one-line summary (counts only) every 5 min."""
    global _last_summary
    key = (data_group or "unknown", message_type or "unknown")
    with _COUNTS_LOCK:
        _COUNTS[key] = _COUNTS.get(key, 0) + 1
        now = time.monotonic()
        first = sum(_COUNTS.values()) == 1
        every = _SUMMARY_EARLY_S if now - _started < _EARLY_WINDOW_S else _SUMMARY_EVERY_S
        if not first and now - _last_summary < every:
            return
        _last_summary = now
        snap = sorted(_COUNTS.items())
    log.info("tfdm: message counts since start: %s",
             ", ".join(f"{dg}/{mt}={n}" for (dg, mt), n in snap))


def counts() -> dict[tuple[str, str], int]:
    with _COUNTS_LOCK:
        return dict(_COUNTS)


def reset_counts() -> None:
    global _last_summary
    with _COUNTS_LOCK:
        _COUNTS.clear()
        _last_summary = time.monotonic()


def _flight_record(fl) -> dict | None:
    ident = None
    for name in ("flightIdentification", "flightIdentificationPrevious"):
        fid = _find(fl, name)
        ident = _attr(fid, "aircraftIdentification")
        if ident:
            break
    dep = _find(fl, "departure")
    dest = _find(fl, "destination")
    arr = _find(fl, "arrival")
    dep_ap = _attr(dep, "departurePointText")
    dest_ap = _attr(dest, "destinationPointText")
    dep_stand = _attr(_find(dep, "standInformation"), "standName") if dep is not None else None
    arr_stand = None
    for side in (arr, dest):
        if side is not None:
            arr_stand = _attr(_find(side, "standInformation"), "standName")
            if arr_stand:
                break
    sched_off = None
    if dep is not None:
        ob = _find(dep, "offBlockTime")
        if ob is not None:
            sched_off = _text(_find(ob, "scheduled")) or _attr(ob, "scheduled")
    tfdm_id = None
    named: dict[str, str] = {}
    afi = _find(fl, "additionalFlightInformation")
    if afi is not None:
        for nv in afi.iter():
            if _local(nv.tag) == "nameValue":
                nm = (_attr(nv, "name") or "").upper().replace(" ", "")
                if nm and nm not in named and _attr(nv, "value"):
                    named[nm] = _attr(nv, "value")
        tfdm_id = named.get("TFDMID")
    if not ident:
        return None
    # 2026-11-19 (operator: TFDM precedes SFDPS before departure). Fields below are read only where
    # the NASA ATD-2 "TTP Flight Data Messages" FIXM table names their path:
    #   AircraftRegistrationMark  flight/aircraft/@registration
    #   EramGufi                  flight/flightPlan/@identifier
    #   Scheduled/Initial/Earliest/Actual off-block  departure/offBlockTime/{scheduled,initial,earliest,actual}
    #   EstimatedTimeOfDeparture / ActualTakeOffTime  departure/runwayDepartureTime/{estimated,actual}
    #   TargetedOffBlockTime      additionalFlightInformation nameValue TOBT
    #   ApprovalRequestReleaseTime  departure/approvalRequestReleaseTime
    #   TmiIdentifiers            departure/departureDelay/@tmiIdentifier (comma-delimited)
    # [VERIFY] ClearanceDeliveryTime: the TTP WSRD lists it ("the time at which a flight received route
    # clearance", TFDM builds 1 and 2) but the ATD-2 FIXM table gives no path; it is looked up by local
    # name and only ever used once the feed has been seen to carry it (common.tfdm_authority).
    # Not in the TTP documents and not read: ICAO 24-bit address, aircraft type, EDCT, CDR.
    # 2026-11-20, checked against 30 live flights (census samples, redacted): runwayDepartureTime/*
    # and runwayArrivalTime/* carry the time in a child <time> (_when); offBlockTime carried only
    # <initial> (no TOBT nameValue on any flight); clearanceDeliveryTime is element text (1 flight);
    # departureDelay carried currentDelay/predictedDelay durations and no @tmiIdentifier; NO flight
    # carried standInformation or aircraft/@registration (3 had an empty <aircraft>). The registration
    # and stand reads stay as documented but are unconfirmed; more samples of watched flights decide.
    reg = _attr(_find(fl, "aircraft"), "registration")
    gufi = _attr(_find(fl, "flightPlan"), "identifier")
    ob = _find(dep, "offBlockTime") if dep is not None else None

    def _ob(name):
        if ob is None:
            return None
        return _when(_find(ob, name)) or _attr(ob, name)

    rdt = _find(dep, "runwayDepartureTime") if dep is not None else None
    tmi = _attr(_find(dep, "departureDelay"), "tmiIdentifier") if dep is not None else None
    apreq = _text(_find(dep, "approvalRequestReleaseTime")) if dep is not None else None
    clr_el = None
    for el in fl.iter():
        if _local(el.tag) in ("clearanceDeliveryTime", "clearanceDelivery"):
            clr_el = el
            break
    clearance = (_text(clr_el) or _attr(clr_el, "time") or "present") if clr_el is not None else None
    return {
        "callsign": ident.upper(),
        "departure_airport": dep_ap.upper() if dep_ap else None,
        "destination_airport": dest_ap.upper() if dest_ap else None,
        "departure_stand": dep_stand,
        "arrival_stand": arr_stand,
        "sched_off_block": sched_off,
        # 2026-11-20 (live samples): the id is flightIdentification/tfdmId, not a nameValue.
        "tfdm_id": tfdm_id or _text(_find(fl, "flightIdentification", "tfdmId")),
        "registration": reg.upper() if reg else None,
        "eram_gufi": gufi,
        "initial_off_block": _ob("initial"),
        "earliest_off_block": _ob("earliest"),
        "actual_off_block": _ob("actual"),
        "tobt": named.get("TOBT"),
        "earliest_takeoff": _when(_find(rdt, "earliest")) if rdt is not None else None,
        "estimated_takeoff": _when(_find(rdt, "estimated")) if rdt is not None else None,
        "actual_takeoff": _when(_find(rdt, "actual")) if rdt is not None else None,
        # 2026-11-20 (live samples): TFDM's own surface state, flightStatus/tfdmFlightState/@value
        # (seen: SCHEDULED, AT_STAND, EN_ROUTE) and the ATC state (seen: GC_PENDING). Not in the
        # ATD-2 FIXM table the first parser was written from.
        "flight_state": (_attr(_find(fl, "flightStatus", "tfdmFlightState"), "value") or "").upper() or None,
        "atc_state": (_attr(_find(fl, "flightStatus", "tfdmAtcFlightState"), "value") or "").upper() or None,
        "apreq_release": apreq,
        "tmi_ids": sorted({t.strip() for t in tmi.split(",") if t.strip()}) if tmi else [],
        "clearance_delivery": clearance,
    }


def parse_ttp_message(payload: bytes, headers: dict | None = None) -> dict:
    """{data_group, message_type, flights: [...]} for one TTP message. Never raises:
    a body that is not XML (or empty, as the header-only sync markers are) comes back
    with no flights and the header's message type."""
    headers = headers or {}
    out = {"data_group": headers.get("DATA_GROUP") or "unknown",
           "message_type": headers.get("MESSAGE_TYPE") or "unknown",
           "flights": []}
    if not payload or not payload.strip():
        return out
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        out["data_group"] = headers.get("DATA_GROUP") or "unparsed"
        return out
    out["data_group"] = _guess_data_group(root, headers)
    if out["message_type"] == "unknown":
        md = _find(root, "metadata")
        out["message_type"] = _attr(md, "messageType") or _local(root.tag) or "unknown"
    if out["data_group"] != "FlightData":
        return out
    flights = [el for el in root.iter() if _local(el.tag) == "flight"]
    for fl in flights:
        rec = _flight_record(fl)
        if rec:
            out["flights"].append(rec)
    return out


def _relevant(rec: dict) -> tuple[bool, dict | None]:
    """(record it?, matching watch entry). DC departures always; elsewhere only a
    watched flight. A matcher failure means 'not watched', never an exception."""
    entry = None
    try:
        from ingest.parsers.smes_parser import _match_watchlist_flight, _departure_side_record
        entry = _match_watchlist_flight(_departure_side_record({
            "callsign": rec["callsign"], "airport": rec.get("departure_airport"),
            "destination_airport": rec.get("destination_airport"),
            "event_time": rec.get("sched_off_block"),
        }))
    except Exception as e:
        log.debug("tfdm: watch match for %s: %s", rec.get("callsign"), type(e).__name__)
    return (rec.get("departure_airport") in DC_AIRPORTS or entry is not None), entry


def record_departure_stands(parsed: dict) -> int:
    """Send each relevant flight's departure stand to common.gate_assignments.
    Returns the number of observations stored. Never raises."""
    n = 0
    for rec in parsed.get("flights") or ():
        if not rec.get("departure_stand") or not rec.get("departure_airport"):
            continue
        ok, _entry = _relevant(rec)
        if not ok:
            continue
        try:
            from common import gate_assignments as ga
            _key, res = ga.record_tfdm(rec)
            if res is not None:
                n += res.stored
        except Exception as e:
            log.debug("tfdm: stand evidence for %s at %s: %s",
                      rec.get("callsign"), rec.get("departure_airport"), type(e).__name__)
    return n


def handle(payload: bytes, headers: dict | None = None) -> bool:
    """The swim_client handler for the tfdm feed. Counts every message; records
    departure stands. True when anything was stored (records_accepted)."""
    parsed = parse_ttp_message(payload, headers)
    count(parsed["data_group"], parsed["message_type"])
    if parsed["data_group"] != "FlightData" or not parsed["flights"]:
        return False
    stored = record_departure_stands(parsed) > 0
    # 2026-11-19: pre-departure identity authority + clearance/TMI/APREQ alerts for WATCHED legs only
    # (common.tfdm_authority). Never raises; a failure leaves stands and counts untouched.
    try:
        from common import tfdm_authority
        for rec in parsed["flights"]:
            if tfdm_authority.process(rec):
                stored = True
    except Exception as e:
        log.debug("tfdm: authority pass: %s", type(e).__name__)
    return stored
