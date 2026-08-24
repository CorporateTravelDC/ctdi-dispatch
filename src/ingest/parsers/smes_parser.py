"""
ingest.parsers.smes_parser -- STDDS surface and terminal track parser.

SMES (Surface Movement Events): ASDE-X surface positions at DCA/IAD/BWI.
TAIS (Terminal Automation Information Service): PCT TRACON radar tracks.
"""
from __future__ import annotations

import logging
import os
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
