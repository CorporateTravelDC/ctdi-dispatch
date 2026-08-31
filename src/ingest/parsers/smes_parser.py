"""
ingest.parsers.smes_parser -- STDDS surface and terminal track parser.

SMES (Surface Movement Events): ASDE-X surface positions at DCA/IAD/BWI.
TAIS (Terminal Automation Information Service): PCT TRACON radar tracks.

Since 2026-08-30 (SWIM ingest audit) this module also parses the four
TDES/APDS shapes confirmed live on the same STDDS queue -- per-runway RVR
(RVRDataUpdateMessage), tower departure events with gate numbers
(TowerDepartureEventMessage), TDLS PDC/CPDLC clearance text
(TDLSCSPMessage), and digital ATIS (DATISData) -- see the APDS/TDES
section at the bottom of this file. Still known-unparsed on this queue
(deliberately, low value): AssetMessage/AssetMonitorMessage and the
various *ServiceStatus/STDDSStatus heartbeat shapes.
"""
from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from common import db
from common.push_dedup import PushDedup, content_hash

log = logging.getLogger("ingest.parsers.smes")

# Full-message debug capture -- 2026-07-20, same technique that confirmed
# tbfm_parser.py's real schema. Originally split "smes" vs "tais" by which
# parse function was called, which conflated everything into two buckets
# regardless of actual message shape.
#
# 2026-08-03 rework: this STDDS queue turns out to carry FAR more message
# shapes than smes/tais -- live samples confirmed asdexMsg (real ASDE-X,
# batched), TATrackAndFlightPlan / TAStatus (TAIS), SafetyLogicHoldBar
# (runway safety-logic status), and DATISData (digital ATIS) all arriving
# on one subscription. Capture is now keyed by the message's ACTUAL root
# tag (not by which parse function happened to be called on it), with a
# small per-tag cap, so a rare/new schema isn't crowded out by whichever
# shape happens to be most common that hour, and future investigation of
# "what else is on this queue" doesn't require guessing from stale
# samples that already rotated out.
_DEBUG_SAMPLE_DIR = "/var/lib/corporatetraveldc/smes_debug"
_DEBUG_SAMPLE_MAX_PER_TAG = 5
_debug_tag_counts: dict[str, int] = {}


def _root_tag_of(xml_bytes: bytes) -> str | None:
    try:
        return ET.fromstring(xml_bytes).tag.split("}")[-1]
    except ET.ParseError:
        return None


# Priority facility capture -- 2026-07-20, fixed 2026-08-03. Generic capture
# above is unfiltered/random, so a message actually sourced from IAD/DCA/BWI
# could be comparatively rare in a random sample even if it exists.
#
# Bug fixed 2026-08-03: this checked `<src>{fac}</src>`, which is the TAIS
# tag -- SMES/asdexMsg messages carry `<airport>{fac}</airport>` instead
# (confirmed via live samples: `<airport>KIAD</airport>`, not `<src>`), so
# the "priority capture for our actual airports" safety net had never once
# matched a real SMES message despite KIAD/KBWI both being confirmed live
# on this queue. Now checks both tag forms.
_PRIORITY_FACILITIES = ("IAD", "DCA", "BWI")
_PRIORITY_SAMPLE_DIR = "/var/lib/corporatetraveldc/smes_debug_priority"
_PRIORITY_SAMPLE_MAX = 25
_priority_debug_count = 0


def _maybe_capture_priority_sample(xml_bytes: bytes, kind: str) -> None:
    global _priority_debug_count
    if _priority_debug_count >= _PRIORITY_SAMPLE_MAX:
        return
    if not any(
        f"<src>{fac}</src>".encode() in xml_bytes or f"<airport>K{fac}</airport>".encode() in xml_bytes
        for fac in _PRIORITY_FACILITIES
    ):
        return
    try:
        os.makedirs(_PRIORITY_SAMPLE_DIR, exist_ok=True)
        path = f"{_PRIORITY_SAMPLE_DIR}/{kind}_priority_{_priority_debug_count}.xml"
        with open(path, "wb") as f:
            f.write(xml_bytes)
        _priority_debug_count += 1
        log.info("stdds: wrote PRIORITY %s debug sample %s (%d bytes)", kind, path, len(xml_bytes))
    except Exception as e:
        log.warning("stdds: priority %s debug sample capture failed: %s", kind, e)


def _capture_debug_sample(xml_bytes: bytes, kind: str) -> None:
    _maybe_capture_priority_sample(xml_bytes, kind)
    tag = _root_tag_of(xml_bytes) or "unknown"
    count = _debug_tag_counts.get(tag, 0)
    if count >= _DEBUG_SAMPLE_MAX_PER_TAG:
        return
    try:
        os.makedirs(_DEBUG_SAMPLE_DIR, exist_ok=True)
        path = f"{_DEBUG_SAMPLE_DIR}/{tag}_{count}.xml"
        with open(path, "wb") as f:
            f.write(xml_bytes)
        log.info("stdds: wrote %s debug sample %s (%d bytes)", tag, path, len(xml_bytes))
    except Exception as e:
        log.warning("stdds: %s debug sample capture failed: %s", tag, e)
    _debug_tag_counts[tag] = count + 1


# AIRPORTS we care about for surface tracks.
SMES_AIRPORTS = frozenset({"KDCA", "KIAD", "KBWI"})
TAIS_FACILITY = "PCT"  # Potomac TRACON

# Namespace prefixes used in STDDS surface/terminal messages (SMES path only
# -- kept for now; SMES has no confirmed real sample yet, see below).
STDDS_NS = {
    "smes": "urn:us:gov:dot:faa:atm:terminal:entities:v2-0:smes:base",
    "tais": "urn:us:gov:dot:faa:atm:terminal:entities:v2-0:tais:base",
    "base": "urn:us:gov:dot:faa:atm:terminal:entities:v2-0:base",
    "ds":   "urn:us:gov:dot:faa:atm:ds",
}


def _ns(tag: str, prefix: str) -> str:
    return f"{{{STDDS_NS[prefix]}}}{tag}"


def _find_any(elem: ET.Element, tag: str) -> ET.Element | None:
    """Try each known namespace prefix, then unqualified."""
    for prefix in STDDS_NS:
        child = elem.find(_ns(tag, prefix))
        if child is not None:
            return child
    return elem.find(tag)


def _text(elem: ET.Element | None, *path: str) -> str | None:
    cur = elem
    for step in path:
        if cur is None:
            return None
        cur = _find_any(cur, step)
    return (cur.text or "").strip() or None if cur is not None else None


def _attr(elem: ET.Element | None, name: str) -> str | None:
    if elem is None:
        return None
    v = elem.get(name)
    return v.strip() if v else None


def _local_tag(tag: str) -> str:
    return tag.split("}")[-1]


def _child_local_text(elem: ET.Element, local_name: str) -> str | None:
    for child in elem:
        if _local_tag(child.tag) == local_name:
            return (child.text or "").strip() or None
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- SMES surface track parser -----------------------------------------------
#
# REAL SCHEMA, confirmed 2026-07-20 two ways: (1) the official FAA
# "FIXM-Mediated STDDS Data Overview" doc's raw-SimpleXML field mapping
# tables, (2) a genuine captured sample (KPHX, not DC-area, but confirms the
# structure lands on this subscription):
#
#   <ns2:asdexMsg xmlns:ns2="...smes:surfacemovementevent">
#     <airport>KPHX</airport>
#     <adsbReport full="false">              <!-- or mlatReport, or positionReport -->
#       <report>
#         <basicReport>
#           <time>2026-07-20T17:05:27.789Z</time>
#           <track>3953</track>              <!-- TRACK_NUMBER -->
#           <position><x>...</x><y>...</y><lat>33.43806</lat><lon>-112.01024</lon></position>
#           <velocity><x>...</x><y>...</y></velocity>   <!-- per doc, not in this sample -->
#         </basicReport>
#         <mode3ACode>...</mode3ACode>       <!-- direct child of report, per doc -->
#         <level>...</level>                 <!-- altitude, direct child of report -->
#       </report>
#       <acAddresss>...</acAddresss>         <!-- direct child of mlatReport/adsbReport, per doc -->
#     </adsbReport>
#     <enhancedData><eramGufi>KS475352JY</eramGufi></enhancedData>
#   </ns2:asdexMsg>
#
# positionReport (SMES Cat11) is FLATTER -- no report/basicReport nesting,
# per doc: /asdexMsg/positionReport/flightId/{acAddress,aircraftId,mode3ACode},
# /asdexMsg/positionReport/flightInfo/acType, /asdexMsg/positionReport/movement/
# {heading,speed,vx,vy}, /asdexMsg/positionReport/position/{altitude,latitude,
# longitude}, /asdexMsg/positionReport/track, /asdexMsg/positionReport/runway.
# No real positionReport sample captured yet (only adsbReport confirmed live) --
# the flat-path handling below is doc-derived, not sample-confirmed; treat as
# higher-confidence-than-guess but still worth a spot-check if one arrives.

def _find_path_local(elem: ET.Element | None, *tags: str) -> ET.Element | None:
    """Walk a chain of direct children by local (namespace-stripped) tag name."""
    cur = elem
    for tag in tags:
        if cur is None:
            return None
        cur = next((c for c in cur if _local_tag(c.tag) == tag), None)
    return cur


def _path_text(elem: ET.Element | None, *tags: str) -> str | None:
    found = _find_path_local(elem, *tags)
    return (found.text or "").strip() or None if found is not None else None


def parse_smes_message(xml_bytes: bytes) -> list[dict]:
    """
    Parse a SMES (Surface Movement Event Service) message -- real asdexMsg
    schema, not the old positionReport/facilityIdentifier tag guesses.

    2026-08-03 fix: a single asdexMsg BATCHES many report elements -- live
    samples confirmed KBWI messages carrying up to 34 positionReports and
    KORD messages carrying up to 51, all as sibling direct children of the
    same asdexMsg root. The original implementation used _find_path_local()
    to grab only the FIRST matching report element and returned a single
    dict, silently discarding the rest of every batch on every message --
    which meant most of each poll's ground traffic (up to ~97% of a
    51-aircraft KORD batch) was thrown away before it ever reached the DB.
    KDCA/KIAD/KBWI still accumulated thousands of surface_tracks rows over
    time purely because *some* aircraft happened to land in the "first
    match" slot across enough messages, masking how much was being dropped
    per cycle. Fixed to walk every batched report element and return one
    dict per valid record.
    """
    _capture_debug_sample(xml_bytes, "smes")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("smes: XML parse error: %s", e)
        return []

    if _local_tag(root.tag) != "asdexMsg":
        return []

    airport = _child_local_text(root, "airport")
    if airport:
        airport = airport.upper().strip()
        if not airport.startswith("K") and len(airport) == 3:
            airport = "K" + airport
    if airport not in SMES_AIRPORTS:
        return []

    report_elems = [
        c for c in root
        if _local_tag(c.tag) in ("positionReport", "mlatReport", "adsbReport")
    ]
    if not report_elems:
        return []

    msg_eram_gufi = _path_text(root, "enhancedData", "eramGufi")

    results: list[dict] = []
    for report_elem in report_elems:
        report_type = _local_tag(report_elem.tag)
        eram_gufi = msg_eram_gufi or _path_text(report_elem, "enhancedData", "eramGufi")

        if report_type == "positionReport":
            # Flat structure -- doc-derived, not yet sample-confirmed.
            track_id = _path_text(report_elem, "track")
            callsign = (_path_text(report_elem, "flightId", "aircraftId")
                        or _path_text(report_elem, "manual", "callNum"))
            squawk = _path_text(report_elem, "flightId", "mode3ACode")
            aircraft_type = (_path_text(report_elem, "flightInfo", "acType")
                              or _path_text(report_elem, "manual", "acType"))
            lat_str = _path_text(report_elem, "position", "latitude")
            lon_str = _path_text(report_elem, "position", "longitude")
            alt_str = _path_text(report_elem, "position", "altitude")
            spd_str = _path_text(report_elem, "movement", "speed")
            hdg_str = _path_text(report_elem, "movement", "heading")
        else:
            # mlatReport / adsbReport (SMES Cat10) -- nested under report/basicReport,
            # confirmed against a real adsbReport sample (KPHX, 2026-07-20).
            inner = _find_path_local(report_elem, "report")
            basic = _find_path_local(inner, "basicReport")
            track_id = _path_text(basic, "track")
            callsign = None  # not present in either the doc's mapping or the real sample
            squawk = _path_text(inner, "mode3ACode")
            aircraft_type = None
            pos = _find_path_local(basic, "position")
            lat_str = _path_text(pos, "lat")
            lon_str = _path_text(pos, "lon")
            alt_str = _path_text(inner, "level")
            vel = _find_path_local(basic, "velocity")
            vx_str = _path_text(vel, "x")
            vy_str = _path_text(vel, "y")
            spd_str = None  # derived below from vx/vy if present, like FDPS's vx/vy fallback
            hdg_str = None
            if vx_str and vy_str:
                try:
                    vx, vy = float(vx_str), float(vy_str)
                    spd_str = str((vx ** 2 + vy ** 2) ** 0.5)
                except ValueError:
                    pass

        if not track_id:
            continue

        try:
            latitude = float(lat_str) if lat_str else None
            longitude = float(lon_str) if lon_str else None
        except ValueError:
            latitude = longitude = None
        if latitude is None or longitude is None:
            continue

        try:
            altitude_ft = float(alt_str) if alt_str else None
        except ValueError:
            altitude_ft = None
        try:
            speed_kts = int(float(spd_str)) if spd_str else None
        except ValueError:
            speed_kts = None
        try:
            heading_deg = float(hdg_str) if hdg_str else None
        except ValueError:
            heading_deg = None

        results.append({
            "track_id": str(track_id),
            "airport": airport,
            "callsign": callsign,
            "squawk": squawk,
            "aircraft_type": aircraft_type,
            "target_type": report_type,
            "latitude": latitude,
            "longitude": longitude,
            "altitude_ft": altitude_ft,
            "speed_kts": speed_kts,
            "heading_deg": heading_deg,
            "eram_gufi": eram_gufi,
            "last_seen": _now_iso(),
        })

    return results


# -- STDDS/TAIS family-wide congestion alert -----------------------------
#
# Added 2026-08-03 per operator direction. Context: TAIS's facility field
# is a hardcoded constant ("PCT" -- Potomac TRACON, see TAIS_FACILITY
# above) -- there is no per-fix, per-taxiway, or per-runway breakdown in
# this feed's data model (no meter-fix field like TBFM, no ground/taxi
# status flag, just track_id/lat/lon/alt/speed/squawk per aircraft). Given
# that, and the operator's own framing ("do we want to just do an overall
# family-wide with no sectors on that?"), this fires a SINGLE family-wide
# "stdds-alerts" topic keyed on overall PCT track-count trend -- how many
# aircraft PCT is working right now vs. its own last-15-minutes baseline --
# rather than inventing a fake per-taxiway/per-fix split this feed can't
# actually support. No "stdds-<zone>" per-sector topic is fired: PCT
# already sits inside the "zdc" ARTCC group used by tbfm/tfms (see
# _ARTCC_GROUPS in shared.sector_coalesce), and since PCT never varies for
# this feed, a zone push would just be a second, redundant copy of the
# exact same aggregate event under a different topic name every time --
# hence fire_family_alert(..., zone_split=False).
#
# Uses the same escalating-burst classification as tbfm/tfms/itws/aim_fns
# (shared.sector_coalesce.record_event: current 15-min window vs. the
# prior 15-min window), gated by a floor so routine single-digit batches
# never even reach the escalation check. Floor set from live samples
# 2026-08-03: normal per-message PCT batches ran 1-20 tracks; 15 sits at
# the high end of routine and low end of "worth a look," leaving the
# escalating-trend gate (not this floor) as the real signal for "PCT is
# genuinely busier than its own recent baseline right now."
_MIN_TAIS_TRACKS_FOR_ALERT = 15


def check_stdds_alerts(tracks: list[dict]) -> None:
    """Fire stdds-alerts (family-wide, no zone split) when this message's
    PCT terminal-track batch size is escalating vs. the last 15 minutes.
    PCT is always DC-local (Potomac TRACON, no nationwide variant) so no
    priority tiering applies here -- only the dedup gate (see
    _STDDS_PCT_DEDUP above)."""
    if not tracks:
        return
    track_count = len(tracks)
    if track_count < _MIN_TAIS_TRACKS_FOR_ALERT:
        return

    dedup_key = content_hash(str(track_count))
    if not _STDDS_PCT_DEDUP.should_push("pct", dedup_key):
        return

    from shared.sector_coalesce import fire_family_alert

    title = "STDDS/TAIS -- Potomac TRACON traffic"
    detail = f"PCT: {track_count} terminal tracks in this update"
    dispatch = f"PCT: {track_count} tracks"
    try:
        result = fire_family_alert(
            "stdds", "stdds", TAIS_FACILITY, title, detail, dispatch,
            base_priority=2, zone_split=False,
        )
        _STDDS_PCT_DEDUP.record("pct", dedup_key)
        log.info(
            "stdds: fire_family_alert for PCT (%d tracks, escalating=%s, "
            "aggregate_fired=%s)",
            track_count, result.get("escalating"), result.get("fired"),
        )
    except Exception as e:
        log.error("stdds: stdds-alert fire failed: %s", e)


# -- Per-airport SMES/ASDE-X surface-track congestion alert ---------------
#
# Added 2026-08-03 per operator direction: "extend that out to the per
# sector, like everything else, so I can see where there are ADSE issues."
# Unlike PCT/TAIS (a single constant facility, hence family-wide-only,
# see check_stdds_alerts above), SMES surface tracks naturally group by
# AIRPORT (KDCA/KIAD/KBWI), and those three airports are genuinely
# distinct facilities worth telling apart -- "DCA ground is busy" and
# "IAD ground is busy" are different, actionable facts, not the same
# event under two names the way stdds-zdc would have been for PCT.
#
# The existing ARTCC-shaped zone machinery (_ARTCC_GROUPS) actually
# collapses DCA/IAD/BWI into the SAME "zdc" group (used by tbfm/tfms),
# which would hide them from each other -- so this uses the new
# sector_override param on fire_family_alert() (2026-08-03) to bypass
# that and use the airport code directly as both the escalation sector
# and the zone topic name: stdds-dca / stdds-iad / stdds-bwi, plus the
# same stdds-alerts family-wide aggregate every other stdds event also
# feeds. Per-airport threshold tuning is available the same way as any
# other zone: shared.sector_coalesce.set_escalate_threshold("stdds",
# "DCA", multiplier, floor) (sector name is the bare airport code minus
# the K prefix, e.g. "DCA" not "KDCA", to match sector_override usage
# below).
_MIN_SURFACE_TRACKS_FOR_ALERT = 15

# STDDS zone routing, extended 2026-08-03 per operator direction: opened
# STDDS alerting up to the same eight ARTCC-level sectors already tracked
# by TBFM/TFMS (shared.sector_coalesce._ARTCC_GROUPS), using real,
# confirmed-live major airports within each sector -- cross-checked
# against this platform's OWN captured data (every airport below has real
# rows in stdds_safety_status/surface_movement_events as of 2026-08-03),
# not guessed from general knowledge. DCA/IAD/BWI keep their EXISTING
# individual, airport-level zone topics (stdds-dca/stdds-iad/stdds-bwi)
# rather than being pooled into one "stdds-zdc" topic like the other seven
# zones -- this is [operator LLC]' own home-region operational
# focus and predates this change (see
# stdds_incursion_taxi_per_airport_zones_20260803). An operator running
# this platform from a different home region should swap
# _STDDS_REGIONAL_AIRPORTS for their own local airports -- nothing
# downstream (topic naming, escalation gating, the ntfy topic-count
# watchdog) treats DCA/IAD/BWI specially in code, only this one constant
# does.
_STDDS_REGIONAL_AIRPORTS = frozenset({"KDCA", "KIAD", "KBWI"})

# Major-airport -> ARTCC-zone-code routing for the other 7 tracked
# sectors. Airport choice per zone is an approximation, not a verified
# ARTCC-boundary lookup (real ARTCC polygons are complex and this
# platform has no boundary-geometry data to check against) -- these are
# well-known major hub airports conventionally associated with each named
# center. "zatl" zone key matches shared.sector_coalesce (real ARTCC code
# there is ZTL, fixed 2026-08-03 -- the zone KEY name is unaffected).
_STDDS_ZONE_AIRPORTS: dict[str, str] = {
    # zny -- New York Center/TRACON
    "KJFK": "zny", "KLGA": "zny", "KEWR": "zny",
    # zid -- Indianapolis Center
    "KCVG": "zid", "KSDF": "zid",
    # zob -- Cleveland Center
    "KCLE": "zob", "KPIT": "zob", "KDTW": "zob",
    # zatl -- Atlanta Center/TRACON
    "KATL": "zatl",
    # zhu -- Houston Center
    "KIAH": "zhu",
    # zla -- Los Angeles Center
    "KLAX": "zla", "KLAS": "zla",
    # zse -- Seattle Center
    "KSEA": "zse", "KPDX": "zse",
}


def _stdds_sector_for(airport: str | None) -> str | None:
    """Return the sector_override value for an airport, or None if it's
    outside the currently-tracked scope (data collection stays nationwide
    regardless -- see write_surface_tracks/write_safety_status/
    write_surface_movement_event, none of which call this; only the
    three alerting functions below do). DCA/IAD/BWI resolve to their own
    bare airport code for an individual zone topic (e.g. stdds-dca); the
    14 other tracked airports resolve to their shared ARTCC-zone code
    (e.g. stdds-zny) so all traffic for one geographic sector coalesces
    onto one topic instead of one-topic-per-airport -- matching the
    TBFM/TFMS convention this was asked to align with, and avoiding the
    per-airport topic-count growth that made incursion/taxi alerting
    nationwide-unscoped a real problem earlier this same session."""
    if not airport:
        return None
    if airport in _STDDS_REGIONAL_AIRPORTS:
        return airport[1:] if airport.startswith("K") and len(airport) == 4 else airport
    return _STDDS_ZONE_AIRPORTS.get(airport)


# -- DC vs. nationwide alert tiering, added 2026-08-05 -----------------------
#
# Context: the client-ack fix (see swim_client.py) stopped stdds from
# crash-looping, which means it now fires its full alert volume continuously
# instead of only in short bursts before dying. A same-night investigation
# found ~574 real alerts in a 55-minute window, 77% of them for the 39
# tracked-but-non-regional airports (everything in _STDDS_ZONE_AIRPORTS)
# rather than DCA/IAD/BWI.
#
# Operator direction: don't silence the nationwide data -- it's the
# "elephant walk" of delayed flights and taxi queues upstream/downstream
# that's genuinely useful for pattern analysis -- tier it instead, the same
# way fdps_parser.py is already scoped: full weight for the DC-local zone,
# lower weight (but not dropped) for everything else. The topic split
# already exists (stdds-dca/iad/bwi vs. the 7 pooled stdds-<zone> topics,
# see _stdds_sector_for above) -- what was missing was a PRIORITY split on
# top of it, since every zone fired at the same base_priority regardless of
# whether it was DC or nationwide.
#
# _stdds_priority() is the one-line hook: DC regional airports keep the
# base_priority a caller passes in; every other tracked airport gets it
# dropped by one level (floored at 1, ntfy's minimum), so nationwide traffic
# still reaches its own zone topic and the family-wide aggregate, just with
# a less intrusive default notification. No fidelity is lost -- this only
# changes the `priority` field on the push, not whether it fires.
def _stdds_priority(airport: str | None, base_priority: int) -> int:
    if airport in _STDDS_REGIONAL_AIRPORTS:
        return base_priority
    return max(1, base_priority - 1)


# Per-topic dedup, added 2026-08-05 -- same PushDedup pattern tbfm_parser.py
# (_TBFM_ALERT_DEDUP) and tfms_parser.py (_TFMS_ALERT_DEDUP) already use to
# collapse bursts of identical-content events before they ever reach
# fire_family_alert(). stdds had zero throttle/dedup of its own before this
# -- every qualifying message re-fired unconditionally (subject only to the
# escalating-trend gate, which answers "is this a real trend" but not "have
# I already told the operator this exact count in the last few minutes").
# 300s mirrors tbfm's window, the closest analog (congestion/volume-based,
# not a one-shot event like a NOTAM). Keyed per-airport (or the constant
# "pct" for the PCT-wide aggregate) so one busy airport's dedup slot can't
# evict another's, same per-entity-key fix tbfm_parser.py needed on
# 2026-07-21. check_incursion_alert is deliberately NOT given a dedup slot
# here -- its previous_bitmask comparison already only fires on a genuine
# content change, and it's explicitly safety-adjacent (escalating_only=False
# for the same reason); a redundant time-based suppression on top of that
# would risk delaying a real runway safety-logic change.
_STDDS_PCT_DEDUP = PushDedup("stdds_pct_alerts", dedup_secs=300)
_STDDS_SURFACE_DEDUP = PushDedup("stdds_surface_alerts", dedup_secs=300)
_STDDS_TAXI_DEDUP = PushDedup("stdds_taxi_alerts", dedup_secs=300)


def check_surface_alerts(tracks: list[dict]) -> None:
    """Fire stdds-alerts (aggregate) + stdds-<zone> (per airport for
    DCA/IAD/BWI, per ARTCC sector for the other 7 tracked zones) when this
    message's SMES surface-track batch size for a given airport is
    escalating vs. that airport's own last-15-minutes baseline.

    FIXED 2026-08-03: this had NO scope check at all before this change --
    unlike check_incursion_alert/check_taxi_alerts (which were caught and
    scoped to DCA/IAD/BWI earlier this session after a live nationwide-
    alerting incident), ASDE-X ground-congestion alerting has been
    unscoped/nationwide since it was first built, one zone topic per
    airport that ever crossed _MIN_SURFACE_TRACKS_FOR_ALERT. That's a
    real, if quieter, version of the same topic-count risk. Now scoped
    via _stdds_sector_for() like the other two, consistent with the
    unified 8-zone design."""
    if not tracks:
        return

    from shared.sector_coalesce import fire_family_alert

    by_airport: dict[str, int] = {}
    for t in tracks:
        airport = t.get("airport")
        if airport:
            by_airport[airport] = by_airport.get(airport, 0) + 1

    for airport, track_count in by_airport.items():
        if track_count < _MIN_SURFACE_TRACKS_FOR_ALERT:
            continue
        sector = _stdds_sector_for(airport)
        if sector is None:
            continue
        dedup_key = content_hash(str(track_count))
        if not _STDDS_SURFACE_DEDUP.should_push(airport, dedup_key):
            continue
        title = f"STDDS/ASDE-X -- {airport} ground traffic"
        detail = f"{airport}: {track_count} surface tracks in this update"
        dispatch = f"{airport}: {track_count} ground tracks"
        try:
            result = fire_family_alert(
                "stdds", "stdds_surface", airport, title, detail, dispatch,
                base_priority=_stdds_priority(airport, 2), sector_override=sector,
            )
            _STDDS_SURFACE_DEDUP.record(airport, dedup_key)
            log.info(
                "stdds: fire_family_alert for %s surface (%d tracks, escalating=%s, "
                "aggregate_fired=%s, zone_fired=%s)",
                airport, track_count, result.get("escalating"),
                result.get("fired"), result.get("zone_fired"),
            )
        except Exception as e:
            log.error("stdds: stdds-surface alert fire failed for %s: %s", airport, e)


def write_surface_tracks(tracks: list[dict]) -> int:
    """Upsert a list of surface track dicts. Returns count written."""
    written = 0
    for t in tracks:
        try:
            db.upsert_surface_track(
                track_id=t["track_id"],
                airport=t["airport"],
                callsign=t.get("callsign"),
                squawk=t.get("squawk"),
                aircraft_type=t.get("aircraft_type"),
                target_type=t.get("target_type"),
                latitude=t["latitude"],
                longitude=t["longitude"],
                altitude_ft=t.get("altitude_ft"),
                speed_kts=t.get("speed_kts"),
                heading_deg=t.get("heading_deg"),
                eram_gufi=t.get("eram_gufi"),
                last_seen=t["last_seen"],
            )
            written += 1
        except Exception as e:
            log.error("smes: DB write error for track %s: %s", t.get("track_id"), e)
    return written


# -- TAIS terminal track parser ----------------------------------------------
#
# REAL SCHEMA, confirmed 2026-07-20 against 5 live captured messages (see
# _capture_debug_sample above). The old tag guesses (TrackPositionEvent,
# facilityIdentifier, aircraftIdentification, modeACode/ssrCode, altitude,
# groundSpeed, latitude/longitude) never matched anything -- that's why
# terminal_tracks stayed 0 rows all session. Real structure:
#
#   <ns2:TATrackAndFlightPlan xmlns:ns2="...tais:terminalautomationinformation">
#     <src>TRI</src>                      <!-- facility, once per message -->
#     <record>
#       <recSeqNum>29566</recSeqNum>
#       <recType>210</recType>            <!-- 210 = surveillance track -->
#       <track>
#         <trackNum>3045</trackNum>
#         <mrtTime>2026-07-20T14:05:44.044Z</mrtTime>
#         <status>active</status>
#         <acAddress>000000</acAddress>   <!-- Mode S address, may be all-0 -->
#         <lat>38.01017</lat>
#         <lon>-82.08506</lon>
#         <reportedBeaconCode>6045</reportedBeaconCode>
#         <reportedAltitude>12900</reportedAltitude>
#         <vx>175</vx><vy>219</vy>        <!-- velocity components, not a
#                                              single "groundSpeed" field -->
#       </track>
#     </record>
#     <record>...</record>  <!-- repeated, many tracks per message -->
#   </ns2:TATrackAndFlightPlan>
#
# No callsign/acid field was present on any recType=210 (surveillance-only)
# record in the captured samples -- TAIS apparently carries flight-plan
# correlation (callsign) on a different recType this capture didn't happen
# to catch. callsign is left None when absent rather than guessed.
#
# All 5 captured samples had <src>TRI</src> (a different TRACON), not PCT --
# so the PCT-only filter below is unverified against a real PCT-sourced
# message, but the container/field-name fix itself is now evidence-based.

def parse_tais_message(xml_bytes: bytes) -> list[dict]:
    """
    Parse a TAIS TATrackAndFlightPlan message. Returns a list of terminal
    track dicts for PCT TRACON (src == "PCT"); other facilities are skipped.
    """
    _capture_debug_sample(xml_bytes, "tais")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("tais: XML parse error: %s", e)
        return []

    if "TATrackAndFlightPlan" not in root.tag:
        return []

    facility = (_child_local_text(root, "src") or "").upper().strip()
    if facility != TAIS_FACILITY:
        return []

    results: list[dict] = []
    for record in root:
        if _local_tag(record.tag) != "record":
            continue
        track_elem = None
        for child in record:
            if _local_tag(child.tag) == "track":
                track_elem = child
                break
        if track_elem is None:
            continue
        track = _parse_tais_track(track_elem, facility)
        if track:
            results.append(track)

    return results


def _parse_tais_track(track_elem: ET.Element, facility: str) -> dict | None:
    track_id = _child_local_text(track_elem, "trackNum")
    if not track_id:
        return None

    lat_str = _child_local_text(track_elem, "lat")
    lon_str = _child_local_text(track_elem, "lon")
    try:
        latitude = float(lat_str) if lat_str else None
        longitude = float(lon_str) if lon_str else None
    except ValueError:
        latitude = longitude = None

    squawk = _child_local_text(track_elem, "reportedBeaconCode")
    mode_s = _child_local_text(track_elem, "acAddress")
    # "000000" is TAIS's placeholder for "no Mode S address available" --
    # normalize to None rather than storing a fake address.
    if mode_s == "000000":
        mode_s = None

    alt_str = _child_local_text(track_elem, "reportedAltitude")
    try:
        altitude_ft = float(alt_str) if alt_str else None
    except ValueError:
        altitude_ft = None

    # Ground speed isn't a direct field -- derive magnitude from vx/vy
    # velocity components (units unconfirmed, likely knots; treated as an
    # approximation, not authoritative).
    vx_str = _child_local_text(track_elem, "vx")
    vy_str = _child_local_text(track_elem, "vy")
    ground_speed = None
    try:
        if vx_str and vy_str:
            ground_speed = int(round((float(vx_str) ** 2 + float(vy_str) ** 2) ** 0.5))
    except ValueError:
        pass

    # No callsign field observed on surveillance-only (recType 210) records
    # in the captured samples -- left None rather than guessed.
    callsign = None

    return {
        "track_id": str(track_id),
        "facility": facility,
        "callsign": callsign,
        "squawk": squawk,
        "mode_s": mode_s,
        "latitude": latitude,
        "longitude": longitude,
        "altitude_ft": altitude_ft,
        "ground_speed": ground_speed,
        "last_seen": _now_iso(),
    }


def write_terminal_tracks(tracks: list[dict]) -> int:
    """Upsert a list of terminal track dicts. Returns count written."""
    written = 0
    for t in tracks:
        try:
            db.upsert_terminal_track(
                track_id=t["track_id"],
                facility=t["facility"],
                callsign=t.get("callsign"),
                squawk=t.get("squawk"),
                mode_s=t.get("mode_s"),
                latitude=t.get("latitude"),
                longitude=t.get("longitude"),
                altitude_ft=t.get("altitude_ft"),
                ground_speed=t.get("ground_speed"),
                last_seen=t["last_seen"],
            )
            written += 1
        except Exception as e:
            log.error("tais: DB write error for track %s: %s", t.get("track_id"), e)
    return written


# -- SafetyLogicHoldBar -- runway safety-logic status / incursion signal ---
#
# Added 2026-08-03 per operator direction: "I can see an incursion alert
# into the system." Real schema confirmed via live samples (KCLT, KCVG):
#
#   <ns2:SafetyLogicHoldBar xmlns:ns2="...v4-0:smes:surfacemovementevent">
#     <airport>KCLT</airport>
#     <control>1</control>
#     <status>0000000000000000000000000000000000000000000000000000800000200000</status>
#   </ns2:SafetyLogicHoldBar>
#
# IMPORTANT LIMITATION, stated plainly rather than glossed over: no FAA
# ICD/interface document is available to this project confirming what each
# digit position in <status> means (which runway, which hold-bar light,
# which sensor). Two samples show <control> as a constant "1" and <status>
# as a long mostly-zero digit string with occasional non-zero digits at
# different positions between airports -- consistent with a per-light or
# per-position bitmask, but that is an inference, not a confirmed mapping.
# Treat a CHANGE in this bitmask as "something in this airport's runway
# safety-logic picture just changed, go look" -- not as a decoded "runway
# incursion detected at taxiway X" classification. Overclaiming precision
# here would be actively misleading for safety-adjacent data.
def parse_safety_logic_message(xml_bytes: bytes) -> dict | None:
    """Parse a SafetyLogicHoldBar message. Returns a dict or None (wrong
    root tag / parse error / no airport)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("stdds: SafetyLogicHoldBar XML parse error: %s", e)
        return None

    if _local_tag(root.tag) != "SafetyLogicHoldBar":
        return None

    airport = _child_local_text(root, "airport")
    if not airport:
        return None
    airport = airport.upper().strip()
    if not airport.startswith("K") and len(airport) == 3:
        airport = "K" + airport

    control = _child_local_text(root, "control")
    status_bitmask = _child_local_text(root, "status")
    if not status_bitmask:
        return None

    return {
        "airport": airport,
        "control": control,
        "status_bitmask": status_bitmask,
        "last_seen": _now_iso(),
    }


def write_safety_status(record: dict) -> str | None:
    """Upsert one SafetyLogicHoldBar record. Returns the PREVIOUS
    status_bitmask (None if this airport is new), so the caller can detect
    a change without a second query."""
    try:
        return db.upsert_safety_status(
            airport=record["airport"],
            control=record.get("control"),
            status_bitmask=record["status_bitmask"],
            last_seen=record["last_seen"],
        )
    except Exception as e:
        log.error("stdds: DB write error for safety status %s: %s", record.get("airport"), e)
        return None


def check_incursion_alert(record: dict, previous_bitmask: str | None) -> None:
    """Fire stdds-alerts (aggregate) + stdds-<zone> when a SafetyLogicHoldBar
    status bitmask CHANGES. escalating_only=False -- a single status
    change is itself worth a look; this is safety-adjacent data and
    should not wait for a 3x-burst trend the way routine traffic volume
    does. Scoped via _stdds_sector_for() -- extended 2026-08-03 from
    DCA/IAD/BWI-only to all 8 tracked zones, see comment above
    _STDDS_REGIONAL_AIRPORTS.

    2026-08-05: base_priority is tiered via _stdds_priority() same as the
    volume alerts below -- this only changes the ntfy priority badge for a
    nationwide airport's change, not whether/when it fires. escalating_only
    stays False and no dedup gate is added (see _STDDS_PCT_DEDUP comment
    above) -- a real safety-logic change still fires immediately regardless
    of DC vs. nationwide."""
    airport = record["airport"]
    sector = _stdds_sector_for(airport)
    if sector is None:
        return
    new_bitmask = record["status_bitmask"]
    if previous_bitmask is not None and previous_bitmask == new_bitmask:
        return  # unchanged -- no alert

    # 2026-08-03: log every real change for DCA/IAD/BWI to the append-only
    # history table (db.STDDS_SAFETY_HISTORY_AIRPORTS) -- separate from
    # and in addition to the alert fired below. This is what lets a later
    # analysis pass correlate bit-position flips against
    # surface_movement_events (same ASDE-X sensor network) to empirically
    # reverse-engineer what each bit means, since no FAA ICD is available.
    # Failure here must never block the alert path below -- history is a
    # nice-to-have research log, not part of the safety-adjacent alerting
    # this function exists for.
    if airport in db.STDDS_SAFETY_HISTORY_AIRPORTS:
        try:
            db.insert_safety_status_history(
                airport=airport,
                control=record.get("control"),
                previous_bitmask=previous_bitmask,
                new_bitmask=new_bitmask,
                changed_at=record["last_seen"],
            )
        except Exception as e:
            log.error("stdds: safety-status history write failed for %s: %s", airport, e)

    from shared.sector_coalesce import fire_family_alert

    if previous_bitmask is None:
        detail = f"{airport}: safety-logic status first seen -- raw bitmask {new_bitmask}"
    else:
        detail = f"{airport}: safety-logic status CHANGED -- raw bitmask {new_bitmask} (was {previous_bitmask})"
    title = f"STDDS Safety Logic -- {airport} status change"
    dispatch = f"{airport}: safety-logic status changed (raw, unconfirmed mapping)"
    try:
        result = fire_family_alert(
            "stdds", "stdds_safety", airport, title, detail, dispatch,
            base_priority=_stdds_priority(airport, 3), escalating_only=False,
            sector_override=sector,
        )
        log.info(
            "stdds: fire_family_alert for %s safety-logic change (aggregate_fired=%s, zone_fired=%s)",
            airport, result.get("fired"), result.get("zone_fired"),
        )
    except Exception as e:
        log.error("stdds: stdds-safety alert fire failed for %s: %s", airport, e)


# -- SurfaceMovementEventMessage -- discrete taxi/ground-event stream ------
#
# Added 2026-08-03 per operator direction: "taxi issues." Real schema
# confirmed via live samples (KMCO, KMSP, KSEA): one aircraft per message,
# a current <event> (e.g. "runwayin", "runwayout") plus a rolling
# <events><eventRecord> history (e.g. "spotout" = gate pushback, "on" =
# touchdown), a <status> of "onrunway" or "onsurface" (taxiing), plus
# <runway>, position, and enhancedData with departure/destination airports.
#
# This is a genuinely different signal from raw ASDE-X track density
# (check_surface_alerts above): it's FAA's own discrete state stream per
# aircraft, so "how many aircraft are CURRENTLY taxiing (status=onsurface)
# at this airport" is a real derived count, not an inference from position
# density alone.
def parse_surface_movement_event_message(xml_bytes: bytes) -> dict | None:
    """Parse a SurfaceMovementEventMessage. Returns a dict or None (wrong
    root tag / parse error / missing required fields)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("stdds: SurfaceMovementEventMessage XML parse error: %s", e)
        return None

    if _local_tag(root.tag) != "SurfaceMovementEventMessage":
        return None

    airport = _child_local_text(root, "airport")
    track_id = _child_local_text(root, "track")
    if not airport or not track_id:
        return None
    airport = airport.upper().strip()
    if not airport.startswith("K") and len(airport) == 3:
        airport = "K" + airport

    callsign = _child_local_text(root, "callsign")
    event = _child_local_text(root, "event")
    status = _child_local_text(root, "status")
    runway = _child_local_text(root, "runway")
    event_time = _child_local_text(root, "time")

    pos = _find_path_local(root, "position")
    lat_str = _path_text(pos, "latitude")
    lon_str = _path_text(pos, "longitude")
    alt_str = _child_local_text(root, "altitude")

    try:
        latitude = float(lat_str) if lat_str else None
        longitude = float(lon_str) if lon_str else None
    except ValueError:
        latitude = longitude = None
    try:
        altitude_ft = float(alt_str) if alt_str else None
    except ValueError:
        altitude_ft = None

    enhanced = _find_path_local(root, "enhancedData")
    departure_airport = _path_text(enhanced, "departureAirport") if enhanced is not None else None
    destination_airport = _path_text(enhanced, "destinationAirport") if enhanced is not None else None

    return {
        "track_id": str(track_id),
        "airport": airport,
        "callsign": callsign,
        "event": event,
        "status": status,
        "runway": runway,
        "latitude": latitude,
        "longitude": longitude,
        "altitude_ft": altitude_ft,
        "event_time": event_time,
        "departure_airport": departure_airport,
        "destination_airport": destination_airport,
        "last_seen": _now_iso(),
    }


def write_surface_movement_event(record: dict) -> bool:
    """Upsert one SurfaceMovementEventMessage record. Returns True on
    success."""
    try:
        db.upsert_surface_movement_event(
            track_id=record["track_id"],
            airport=record["airport"],
            callsign=record.get("callsign"),
            event=record.get("event"),
            status=record.get("status"),
            runway=record.get("runway"),
            latitude=record.get("latitude"),
            longitude=record.get("longitude"),
            altitude_ft=record.get("altitude_ft"),
            event_time=record.get("event_time"),
            departure_airport=record.get("departure_airport"),
            destination_airport=record.get("destination_airport"),
            last_seen=record["last_seen"],
        )
        return True
    except Exception as e:
        log.error("stdds: DB write error for surface movement event %s: %s", record.get("track_id"), e)
        return False


# 2026-08-30 (operator directive, following up on the surface-events-are-
# already-parsed-but-only-used-for-an-aggregate-count gap): map SMES's
# discrete event/status pairs onto the OOOI phases they actually
# represent. Matches this parser's own docstring above
# parse_surface_movement_event_message() (spotout=gate pushback, on=
# touchdown) -- not copied from any external source, this vocabulary was
# already independently documented here. runwayin/runwayout are
# deliberately absent: those are mid-taxi runway-crossing events, not a
# gate/wheels transition, and don't correspond to any OOOI phase.
_SMES_OOOI_MAP: dict[tuple[str, str], str] = {
    ("spotout", "onsurface"): "out",   # left the gate/ramp spot -- pushback complete
    ("off", "airborne"): "off",         # wheels up
    ("on", "onrunway"): "on",           # touchdown
    ("on", "onsurface"): "on",          # touchdown, already clear of the runway surface
    ("spotin", "onramp"): "in",         # entered the gate/ramp spot
}


def _match_watchlist_flight(identifier: str | None) -> dict | None:
    """Find an active flight watchlist entry matching this identifier
    (callsign), case-insensitive. Same matching convention as
    tbfm_parser.py's/tfms_parser.py's own copies (kept file-local rather
    than shared -- each ingest parser already does this)."""
    if not identifier:
        return None
    try:
        from shared.watchlist import get_active_entries
        entries = get_active_entries(entry_type="flight")
    except Exception as e:
        log.error("stdds: watchlist lookup failed: %s", e)
        return None
    ident_upper = identifier.upper().strip()
    for entry in entries:
        if entry["identifier"].upper() == ident_upper:
            return entry
    return None


_SMES_OOOI_DEDUP = PushDedup("smes_oooi", dedup_secs=1800)


def check_smes_watchlist_oooi(record: dict) -> None:
    """Advance a watched flight's authoritative OOOI phase from a real
    ASDE-X-observed surface event, instead of waiting on TFMS's slower,
    self-reported airlineOutTime/OffTime/OnTime/InTime (see
    tfms_parser.py's _handle_flight_times) -- surface events are FAA's
    own ground-radar observation of the actual physical event and
    typically land minutes ahead of the airline's own report for the
    same transition.

    Deliberately does NOT touch _OOOI_SOURCE_PRIORITY/_OOOI_PHASE_ORDER
    semantics or update_watchlist_oooi_phase_authoritative() itself --
    that authority-gating logic (forward-only, source-priority-ranked)
    already handles this call safely: a stale/regressive event is
    rejected on its own, so this function can call it unconditionally
    whenever the event/status pair maps to a known phase.

    30-min dedup per (entry, phase) -- an aircraft only genuinely
    transitions through each OOOI phase once, so a second SMES message
    reporting the same phase (a resend, or a lower-authority re-confirm)
    shouldn't re-fire a watchlist notification, matching
    tbfm_parser.py's _check_tbfm_watchlist_hits' identical reasoning.
    """
    event = (record.get("event") or "").strip().lower()
    status = (record.get("status") or "").strip().lower()
    phase = _SMES_OOOI_MAP.get((event, status))
    if phase is None:
        return

    entry = _match_watchlist_flight(record.get("callsign"))
    if entry is None:
        return

    updated_at = record.get("event_time") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        applied = db.update_watchlist_oooi_phase_authoritative(
            entry["id"], phase, source="smes", updated_at=updated_at,
        )
    except Exception as e:
        log.error("stdds: authoritative OOOI update failed for %s: %s", record.get("callsign"), e)
        return
    if not applied:
        return  # regressive, or already confirmed by an equal/higher-authority source

    dedup_key = content_hash(f"smes_oooi:{entry['id']}:{phase}")
    if not _SMES_OOOI_DEDUP.should_push(entry["id"], dedup_key):
        return

    try:
        from shared.watchlist import watchlist_event_hit
        watchlist_event_hit(
            entry["id"],
            f"{record.get('callsign')} OOOI ({phase.upper()}): surface-observed at "
            f"{record.get('airport')}{' rwy ' + record['runway'] if record.get('runway') else ''}",
            {"watchlist_trigger": "smes_oooi", "phase": phase, "event": event,
             "status": status, "airport": record.get("airport"),
             "runway": record.get("runway")},
            priority=3,
        )
    except Exception as e:
        log.error("stdds: watchlist_event_hit failed for %s: %s", record.get("callsign"), e)

    _SMES_OOOI_DEDUP.record(entry["id"], dedup_key)


_MIN_ONSURFACE_FOR_ALERT = 10


def check_taxi_alerts(record: dict) -> None:
    """Fire stdds-alerts (aggregate) + stdds-<zone> when the CURRENT count
    of aircraft in taxi phase (status='onsurface') at this airport is
    escalating vs. its own last-15-minutes baseline. Only queries/fires on
    the airport touched by this message, not a full nationwide sweep.
    Scoped via _stdds_sector_for() -- extended 2026-08-03 from
    DCA/IAD/BWI-only to all 8 tracked zones, see comment above
    _STDDS_REGIONAL_AIRPORTS."""
    airport = record.get("airport")
    sector = _stdds_sector_for(airport) if airport else None
    if sector is None:
        return

    onsurface_count = db.count_onsurface(airport)
    if onsurface_count < _MIN_ONSURFACE_FOR_ALERT:
        return

    dedup_key = content_hash(str(onsurface_count))
    if not _STDDS_TAXI_DEDUP.should_push(airport, dedup_key):
        return

    from shared.sector_coalesce import fire_family_alert

    sector = airport[1:] if airport.startswith("K") and len(airport) == 4 else airport
    title = f"STDDS Taxi -- {airport} ground movement"
    detail = f"{airport}: {onsurface_count} aircraft currently taxiing (status=onsurface)"
    dispatch = f"{airport}: {onsurface_count} taxiing"
    try:
        result = fire_family_alert(
            "stdds", "stdds_taxi", airport, title, detail, dispatch,
            base_priority=_stdds_priority(airport, 2), sector_override=sector,
        )
        _STDDS_TAXI_DEDUP.record(airport, dedup_key)
        log.info(
            "stdds: fire_family_alert for %s taxi (%d onsurface, escalating=%s, "
            "aggregate_fired=%s, zone_fired=%s)",
            airport, onsurface_count, result.get("escalating"),
            result.get("fired"), result.get("zone_fired"),
        )
    except Exception as e:
        log.error("stdds: stdds-taxi alert fire failed for %s: %s", airport, e)


# ── APDS / TDES message family (RVR, D-ATIS, TDLS clearances, departure
#    events) -- added 2026-08-30 (SWIM ingest audit) ─────────────────────────
#
# The tag-keyed debug capture above (2026-08-03 rework) had already proven
# these four message shapes arrive continuously on this same STDDS queue --
# smes_debug/ holds real captured samples of every one of them
# (RVRDataUpdateMessage_*, DATISData_*, TDLSCSPMessage_*,
# TowerDepartureEventMessage_*) -- but _handle_stdds_message's dispatch
# chain only ever tried asdexMsg/TAIS/SafetyLogicHoldBar/
# SurfaceMovementEventMessage and let everything else fall through the
# final `return False`, dropped unparsed. This section adds one combined
# root-tag dispatcher for the four TDES/APDS shapes.
#
# Field mappings below are derived from THIS BOX's own captured samples
# (paths cited per function), not from the external "unread SWIM fields"
# document that prompted the audit -- that document's specific claims were
# only trusted where a real local capture agreed with them.
#
# Scope: rows are stored for DC-area airports (SMES_AIRPORTS) -- these are
# nationwide streams and e.g. PDC text for every US tower is unbounded
# growth for zero dispatch value -- EXCEPT that a TDES/TDLS message for a
# watchlisted callsign is stored (and fires a watchlist hit) regardless of
# airport, matching how every other parser treats watched flights as
# always-relevant.

_TDES_WATCHLIST_DEDUP = PushDedup("tdes_watchlist", dedup_secs=1800)
_TDLS_WATCHLIST_DEDUP = PushDedup("tdls_watchlist", dedup_secs=1800)

# RVR trend characters: FAA docs describe U/D/S(/N), but the live feed
# sends +/-/S/blank (confirmed in every captured RVRDataUpdateMessage_*
# sample: "+" and " "). Normalized to U/D/S at parse time; blank/unknown
# -> None.
_RVR_TREND_MAP = {"U": "U", "D": "D", "S": "S", "N": "S", "+": "U", "-": "D"}


def _rvr_value_ft(raw: str | None) -> int | None:
    """APDS RVR values arrive in HUNDREDS of feet ('60' = 6000 ft --
    consistent with every captured sample, where '60' appears alongside
    clear-weather METARs; RVR's max reportable value is 6000+ ft).
    Blank ('  ') means the sensor isn't reporting that position --
    confirmed directly in captured samples (midpoint blank while
    touchdown/rollout report). '00'/0 is also treated as no-report rather
    than zero visibility: RVR's minimum reportable value is ~100 ft, a
    literal 0 is not a real observation, and defaulting a dead sensor to
    worst-possible visibility would poison any downstream consumer (e.g.
    a future CPS integration) in the dangerous direction. Returns feet or
    None."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or not raw.isdigit():
        return None
    v = int(raw)
    if v <= 0:
        return None
    return v * 100


def _rvr_trend(raw: str | None) -> str | None:
    if not raw:
        return None
    return _RVR_TREND_MAP.get(raw.strip().upper() or None)


def _norm_k_airport(code: str | None) -> str | None:
    if not code:
        return None
    code = code.upper().strip()
    if len(code) == 3:
        code = "K" + code
    return code or None


def _tdls_time_to_iso(raw: str | None) -> str | None:
    """TDLS <time> is MMDDYYYYHHMMSS (confirmed: captured TDLSCSPMessage
    carried 08302026124553 and was received 2026-08-30 12:45:53Z).
    Returns ISO 8601 Z, or the raw string if it doesn't fit that shape
    (never None-out a timestamp we did receive)."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = datetime.strptime(raw, "%m%d%Y%H%M%S").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return raw


# ── TDLS PDC/DCL body parsing -- added 2026-08-30 afternoon pass ─────────────
#
# Every pattern below is derived from REAL captured bodies on this box
# (smes_debug/TDLSCSPMessage_{0..4}.xml plus the first live tdls_messages
# rows written earlier today: KIAD UAL1952, KMIA SWA3740, KMCI SWA2487,
# KSLC DAL2282, KPHX AAL501), NOT from any external document's claimed
# formats. Observed body shapes:
#
#   CPDLC DCL (the only clearance shape captured so far):
#     "003 CPDLC DCL DISPATCH MSG - NOT TO BE USED AS A CLEARANCE SWA3740
#      KMIA B738/L P1725 /AN N8569Z 00Y FL370 CLEARED TO KBWI AIRPORT
#      ALTNN2.DUCEN THEN AS FILED CLIMB VIA SID   DEP FREQ 126.85 GROUND
#      CTRL FREQ 121.8 KMIA.ALTNN2.DUCEN.Q87...RAVNN9.KBWI"
#     variants observed: "PILOT RESPONSE - WILCO" prefix block, "MODIFIED
#     RTE", "MAINT 3000FT" / "MAINTAIN 240KTS MAINT 9000FT" instead of
#     "CLIMB VIA SID", "DEP FREQ SEE SID", "EXPECT RWY 16L".
#   Non-clearance administrative shapes (e.g. KPHX "AAL501 001 N303RG
#     KPHX GA26") parse to all-None -- the raw body is always stored
#     verbatim regardless, so nothing is lost on a shape this misses.
#
# EDCT: confirmed real -- the morning pass's own committed capture
# (tests/ingest/fixtures/swim_audit/TDLSCSPMessage_0.xml, UAL1803 KPIT)
# carries "REVISED EDCT 1330", which the "EDCT hhmm" regex below matches.
# That same body also shows the SID as a standalone token with no dotted
# route string at all ("... FL360 PIT5 CLIMB VIA SID ..."), hence the
# route-less SID fallback in parse_tdls_dcl_body().

# "\*?" -- a REVISED RTE body marks the origin airport with an asterisk
# ("KMIA*.FOLZZ3.ALYRA..GRUBR.Y299...CAPSS4.KDCA", real AAL861 body
# captured live 2026-08-30); the asterisk is stripped for element parsing
# but the route is stored as matched.
_TDLS_ROUTE_RE = re.compile(r"\bK[A-Z]{3}\*?(?:\.{1,2}[A-Z0-9]+)+")
_TDLS_AIRWAY_RE = re.compile(r"^[QJVT]\d+[A-Z]?$")
_TDLS_PATTERNS = {
    # "003 CPDLC DCL DISPATCH MSG" / hypothetical "PDC" bodies
    "response_type": re.compile(r"\bPILOT RESPONSE\s*-\s*([A-Z]+)\b"),
    "registration": re.compile(r"/AN\s+(N[A-Z0-9]{2,6})\b"),
    "cleared_to": re.compile(r"\bCLEARED TO\s+(K[A-Z]{3})\s+AIRPORT\b"),
    "expected_runway": re.compile(r"\bEXPECT RWY\s+(\d{1,2}[LRC]?)\b"),
    # anchored to the "TYPE/suffix Phhmm" shape ("B738/L P1725") so a bare
    # P#### elsewhere in free text can't false-positive
    "proposed_dep_time": re.compile(r"/[A-Z]\s+P(\d{4})\b"),
    "initial_altitude_ft": re.compile(r"\bMAINT\s+(\d{3,5})\s?FT\b"),
    "cruise_fl": re.compile(r"\b(FL\d{2,3})\b"),
    "dep_frequency": re.compile(r"\bDEP FREQ\s+(\d{3}\.\d{1,3})\b"),
    "edct_time": re.compile(r"\bEDCT\s+(\d{4})\b"),
}


def parse_tdls_dcl_body(body: str | None) -> dict:
    """Best-effort field extraction from a raw TDLS dataBody. Returns a
    dict whose keys match the v42 tdls_messages parsed columns; every
    value is None when the pattern isn't present. Pure function, never
    raises on any input -- the caller stores the raw body verbatim either
    way, so a miss here costs queryability, not data."""
    out: dict = {
        "dcl_type": None, "response_type": None, "registration": None,
        "cleared_to": None, "sid": None, "sid_transition": None,
        "expected_runway": None, "climb_via_sid": None,
        "initial_altitude_ft": None, "cruise_fl": None,
        "dep_frequency": None, "proposed_dep_time": None,
        "edct_time": None, "route_text": None,
    }
    if not body:
        return out
    text = " ".join(body.split()).upper()

    if "CPDLC DCL" in text:
        out["dcl_type"] = "CPDLC_DCL"
    elif re.search(r"\bPDC\b", text):
        # not yet observed live -- kept so a plain-PDC tower doesn't land
        # unlabeled the day one shows up
        out["dcl_type"] = "PDC"

    for field, pat in _TDLS_PATTERNS.items():
        m = pat.search(text)
        if m:
            out[field] = m.group(1)
    if out["initial_altitude_ft"] is not None:
        try:
            out["initial_altitude_ft"] = int(out["initial_altitude_ft"])
        except ValueError:
            out["initial_altitude_ft"] = None
    if "CLIMB VIA SID" in text:
        out["climb_via_sid"] = 1

    # Full cleared route: the longest dotted K###.-prefixed token ("KMCI.
    # LAKES5.COU..STL.J24...KBWI"). Longest wins because the SID.TRANS
    # fragment repeated elsewhere ("LAKES5.COU THEN AS FILED") must not
    # shadow the real route string.
    routes = _TDLS_ROUTE_RE.findall(text)
    if routes:
        route = max(routes, key=len)
        if route.count(".") >= 2:
            out["route_text"] = route
            # SID = element after the origin airport when it carries the
            # RNAV-SID trailing digit ("JCOBY4"); transition = the next
            # element unless it's an airway designator (Q/J/V/T + number).
            elems = [e for e in route.replace("*", "").replace("..", ".").split(".") if e]
            if len(elems) >= 2 and re.fullmatch(r"[A-Z]{3,6}\d", elems[1]):
                out["sid"] = elems[1]
                if len(elems) >= 3 and not _TDLS_AIRWAY_RE.match(elems[2]):
                    out["sid_transition"] = elems[2]
    if out["sid"] is None:
        # No usable route-derived SID. Two real fallback shapes:
        #   "FOLZZ3.ALYRA CLIMB VIA SID" / "LAKES5.COU THEN AS FILED" --
        #     the SID.TRANSITION fragment restated before the climb/route
        #     instruction (AAL861 / SWA2487 live bodies);
        #   "... FL360 PIT5 CLIMB VIA SID ..." -- a standalone SID with no
        #     dotted route anywhere in the body (UAL1803 KPIT capture).
        # Anchoring on the instruction keywords keeps either from matching
        # arbitrary alphanumerics in free text.
        m = re.search(r"\b([A-Z]{2,6}\d)(?:\.([A-Z]{2,6}\d?))?\s+"
                      r"(?:CLIMB VIA\b|MAINT\b|THEN AS FILED\b)", text)
        if m:
            out["sid"] = m.group(1)
            if m.group(2) and not _TDLS_AIRWAY_RE.match(m.group(2)):
                out["sid_transition"] = m.group(2)
    return out


def parse_tdes_apds_message(xml_bytes: bytes) -> dict | None:
    """Root-tag dispatcher for the four TDES/APDS shapes confirmed live on
    this queue. Returns {"kind": <rvr|tdes_departure|tdls|datis>, ...} or
    None (any other root tag / parse error). Pure parse -- no DB writes,
    no alerts (see handle_tdes_apds_record)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    tag = _local_tag(root.tag)

    if tag == "RVRDataUpdateMessage":
        # Real sample: smes_debug/RVRDataUpdateMessage_0.xml --
        # airport, then repeating runwayData > runwayID>{numericRunwayID,
        # runwaySubID}, {touchdown,midpoint,rollout}VisualRange + *Trend,
        # runwayEdgeLightSetting, runwayCenterLineLightSetting.
        airport = _norm_k_airport(_child_local_text(root, "airport"))
        if not airport:
            return None
        runways: list[dict] = []
        for rd in root:
            if _local_tag(rd.tag) != "runwayData":
                continue
            rid = _find_path_local(rd, "runwayID")
            num = _path_text(rid, "numericRunwayID") or ""
            sub = _path_text(rid, "runwaySubID") or ""
            runway = (num + sub).strip()
            if not runway:
                continue
            runways.append({
                "runway": runway,
                "touchdown_rvr_ft": _rvr_value_ft(_child_local_text(rd, "touchdownVisualRange")),
                "touchdown_trend": _rvr_trend(_child_local_text(rd, "touchdownTrend")),
                "midpoint_rvr_ft": _rvr_value_ft(_child_local_text(rd, "midpointVisualRange")),
                "midpoint_trend": _rvr_trend(_child_local_text(rd, "midpointTrend")),
                "rollout_rvr_ft": _rvr_value_ft(_child_local_text(rd, "rolloutVisualRange")),
                "rollout_trend": _rvr_trend(_child_local_text(rd, "rolloutTrend")),
                "edge_light_setting": _child_local_text(rd, "runwayEdgeLightSetting"),
                "centerline_light_setting": _child_local_text(rd, "runwayCenterLineLightSetting"),
            })
        if not runways:
            return None
        return {"kind": "rvr", "airport": airport, "runways": runways,
                "last_seen": _now_iso()}

    if tag == "TowerDepartureEventMessage":
        # Real sample: smes_debug/TowerDepartureEventMessage_0.xml --
        # eventTime, aircraftID, beaconCode, aircraftType, computerID,
        # departureAirport, clearanceDeliveryTime, parkingGate,
        # enhancedData > eramGufi/sfdpsGufi/destinationAirport.
        callsign = _child_local_text(root, "aircraftID")
        airport = _norm_k_airport(_child_local_text(root, "departureAirport"))
        event_time = _child_local_text(root, "eventTime")
        if not callsign or not airport or not event_time:
            return None
        enhanced = _find_path_local(root, "enhancedData")
        return {
            "kind": "tdes_departure",
            "airport": airport,
            "callsign": callsign.upper(),
            "event_time": event_time,
            "beacon_code": _child_local_text(root, "beaconCode"),
            "aircraft_type": _child_local_text(root, "aircraftType"),
            "computer_id": _child_local_text(root, "computerID"),
            "clearance_delivery_time": _child_local_text(root, "clearanceDeliveryTime"),
            "parking_gate": _child_local_text(root, "parkingGate"),
            "eram_gufi": _path_text(enhanced, "eramGufi") if enhanced is not None else None,
            "sfdps_gufi": _path_text(enhanced, "sfdpsGufi") if enhanced is not None else None,
            "destination_airport": _norm_k_airport(
                _path_text(enhanced, "destinationAirport") if enhanced is not None else None),
            "last_seen": _now_iso(),
        }

    if tag == "TDLSCSPMessage":
        # Real sample: smes_debug/TDLSCSPMessage_0.xml -- time
        # (MMDDYYYYHHMMSS), airportID, aircraftID, beaconCode,
        # aircraftType, computerID, dataHeader, dataBody (raw PDC/CPDLC/
        # dispatch text -- stored verbatim; since the 2026-08-30 afternoon
        # pass ALSO regex-parsed via parse_tdls_dcl_body(), the morning
        # pass's deliberate raw-only non-goal now being done against the
        # first real accumulated bodies), enhancedData as above.
        airport = _norm_k_airport(_child_local_text(root, "airportID"))
        if not airport:
            return None
        enhanced = _find_path_local(root, "enhancedData")
        callsign = _child_local_text(root, "aircraftID")
        data_body = _child_local_text(root, "dataBody")
        return {
            "kind": "tdls",
            "airport": airport,
            "callsign": callsign.upper() if callsign else None,
            "message_time": _tdls_time_to_iso(_child_local_text(root, "time")),
            "beacon_code": _child_local_text(root, "beaconCode"),
            "aircraft_type": _child_local_text(root, "aircraftType"),
            "computer_id": _child_local_text(root, "computerID"),
            "data_header": _child_local_text(root, "dataHeader"),
            "data_body": data_body,
            "parsed": parse_tdls_dcl_body(data_body),
            "eram_gufi": _path_text(enhanced, "eramGufi") if enhanced is not None else None,
            "sfdps_gufi": _path_text(enhanced, "sfdpsGufi") if enhanced is not None else None,
            "destination_airport": _norm_k_airport(
                _path_text(enhanced, "destinationAirport") if enhanced is not None else None),
            "last_seen": _now_iso(),
        }

    if tag == "DATISData":
        # Real sample: smes_debug/DATISData_0.xml -- airportID, DATISTime,
        # editType, atisCode, dataBody (the full D-ATIS broadcast text,
        # which carries the active runway configuration in prose, e.g.
        # "ILS RY 22 APCH IN USE LND RY 22. DEPART RY 31.").
        airport = _norm_k_airport(_child_local_text(root, "airportID"))
        if not airport:
            return None
        return {
            "kind": "datis",
            "airport": airport,
            "atis_code": _child_local_text(root, "atisCode"),
            "edit_type": _child_local_text(root, "editType"),
            "datis_time": _child_local_text(root, "DATISTime"),
            "body": _child_local_text(root, "dataBody"),
            "last_seen": _now_iso(),
        }

    return None


def handle_tdes_apds_record(rec: dict) -> bool:
    """Persist one parse_tdes_apds_message() result and fire the watchlist
    hit where applicable. Returns True if anything was written (feeds the
    records_accepted counter in swim_client). All DB/notify failures are
    caught and logged -- this runs inside the live ingest loop and must
    never raise."""
    from common import db_swim

    kind = rec.get("kind")

    if kind == "rvr":
        if rec["airport"] not in SMES_AIRPORTS:
            return False
        written = 0
        for rw in rec["runways"]:
            try:
                db_swim.upsert_stdds_rvr(
                    airport=rec["airport"], runway=rw["runway"],
                    touchdown_rvr_ft=rw["touchdown_rvr_ft"],
                    touchdown_trend=rw["touchdown_trend"],
                    midpoint_rvr_ft=rw["midpoint_rvr_ft"],
                    midpoint_trend=rw["midpoint_trend"],
                    rollout_rvr_ft=rw["rollout_rvr_ft"],
                    rollout_trend=rw["rollout_trend"],
                    edge_light_setting=rw["edge_light_setting"],
                    centerline_light_setting=rw["centerline_light_setting"],
                    last_seen=rec["last_seen"],
                )
                written += 1
            except Exception as e:
                log.error("stdds: RVR write failed for %s rwy %s: %s",
                          rec["airport"], rw.get("runway"), e)
        return written > 0

    if kind == "tdes_departure":
        entry = _match_watchlist_flight(rec.get("callsign"))
        dc_relevant = (rec["airport"] in SMES_AIRPORTS
                       or rec.get("destination_airport") in SMES_AIRPORTS)
        if entry is None and not dc_relevant:
            return False
        stored = False
        try:
            stored = db_swim.insert_tdes_departure_event(
                airport=rec["airport"], callsign=rec["callsign"],
                event_time=rec["event_time"], beacon_code=rec.get("beacon_code"),
                aircraft_type=rec.get("aircraft_type"),
                computer_id=rec.get("computer_id"),
                clearance_delivery_time=rec.get("clearance_delivery_time"),
                parking_gate=rec.get("parking_gate"),
                eram_gufi=rec.get("eram_gufi"), sfdps_gufi=rec.get("sfdps_gufi"),
                destination_airport=rec.get("destination_airport"),
                last_seen=rec["last_seen"],
            )
        except Exception as e:
            log.error("stdds: TDES departure-event write failed for %s at %s: %s",
                      rec.get("callsign"), rec.get("airport"), e)
        if entry is not None:
            # The gate number + clearance-delivery time, before pushback --
            # exactly the curb-side detail a dispatcher can act on. 30-min
            # dedup per (entry, event content) so a rebroadcast doesn't
            # re-fire, matching _check_tbfm_watchlist_hits' cadence.
            dedup_key = content_hash(
                f"tdes:{entry['id']}:{rec['event_time']}:{rec.get('parking_gate')}")
            if _TDES_WATCHLIST_DEDUP.should_push(entry["id"], dedup_key):
                gate = rec.get("parking_gate")
                summary = (f"{rec['callsign']} clearance delivered at {rec['airport']}"
                           + (f", gate {gate}" if gate else "")
                           + (f" -> {rec['destination_airport']}"
                              if rec.get("destination_airport") else ""))
                try:
                    from shared.watchlist import watchlist_event_hit
                    watchlist_event_hit(
                        entry["id"], summary,
                        {"watchlist_trigger": "tdes_departure",
                         "airport": rec["airport"], "parking_gate": gate,
                         "clearance_delivery_time": rec.get("clearance_delivery_time"),
                         "destination": rec.get("destination_airport"),
                         "beacon_code": rec.get("beacon_code")},
                        priority=3,
                    )
                    _TDES_WATCHLIST_DEDUP.record(entry["id"], dedup_key)
                except Exception as e:
                    log.error("stdds: TDES watchlist hit failed for %s: %s",
                              rec.get("callsign"), e)
        return stored

    if kind == "tdls":
        entry = _match_watchlist_flight(rec.get("callsign"))
        dc_relevant = (rec["airport"] in SMES_AIRPORTS
                       or rec.get("destination_airport") in SMES_AIRPORTS)
        if entry is None and not dc_relevant:
            return False
        try:
            db_swim.insert_tdls_message(
                airport=rec["airport"], callsign=rec.get("callsign"),
                message_time=rec.get("message_time"),
                beacon_code=rec.get("beacon_code"),
                aircraft_type=rec.get("aircraft_type"),
                computer_id=rec.get("computer_id"),
                data_header=rec.get("data_header"),
                data_body=rec.get("data_body"),
                eram_gufi=rec.get("eram_gufi"), sfdps_gufi=rec.get("sfdps_gufi"),
                destination_airport=rec.get("destination_airport"),
                received_at=rec["last_seen"],
                parsed=rec.get("parsed"),
            )
        except Exception as e:
            log.error("stdds: TDLS write failed for %s at %s: %s",
                      rec.get("callsign"), rec.get("airport"), e)
            return False
        if entry is not None:
            body = " ".join((rec.get("data_body") or "").split())
            dedup_key = content_hash(f"tdls:{entry['id']}:{body}")
            if _TDLS_WATCHLIST_DEDUP.should_push(entry["id"], dedup_key):
                summary = (f"{rec.get('callsign')} TDLS message at {rec['airport']}: "
                           f"{body[:140]}" + ("…" if len(body) > 140 else ""))
                try:
                    from shared.watchlist import watchlist_event_hit
                    watchlist_event_hit(
                        entry["id"], summary,
                        {"watchlist_trigger": "tdls_clearance",
                         "airport": rec["airport"],
                         "destination": rec.get("destination_airport"),
                         "message_time": rec.get("message_time")},
                        priority=3,
                    )
                    _TDLS_WATCHLIST_DEDUP.record(entry["id"], dedup_key)
                except Exception as e:
                    log.error("stdds: TDLS watchlist hit failed for %s: %s",
                              rec.get("callsign"), e)
        return True

    if kind == "datis":
        if rec["airport"] not in SMES_AIRPORTS:
            return False
        try:
            db_swim.upsert_datis_snapshot(
                airport=rec["airport"], atis_code=rec.get("atis_code"),
                edit_type=rec.get("edit_type"), datis_time=rec.get("datis_time"),
                body=rec.get("body"), last_seen=rec["last_seen"],
            )
            return True
        except Exception as e:
            log.error("stdds: D-ATIS write failed for %s: %s", rec.get("airport"), e)
            return False

    return False
