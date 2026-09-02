"""
ingest.parsers.tbfm_parser — FAA TBFM (Time-Based Flow Management) NMS parser.

TBFM delivers arrival sequencing and metering data for DC-area airports:
  - Meter fix ETAs and sequence numbers (DCA/IAD/BWI arrival streams)
  - Assigned crossing times at meter fixes (LUCIT, SWANN, RAVNN, etc.)
  - Speed assignments from TBFM automation

Data is written to the tbfm_sequences table. Unlike GDP/GS programs from TFMS,
TBFM data is not available via any REST API — NMS is the only source.

Heartbeat key: "tbfm" (no REST fallback; when NMS is down this data is simply absent)

DC-area meter fixes for IAD/DCA approaches:
  IAD: LUCIT, SWANN, RAVNN, FLUKY, SFARA
  DCA: JIMBO, WAVER, WOOLY
  BWI: PALEO, MERIT

--- REAL SCHEMA, confirmed 2026-07-20 against live captured samples ---
(previous exact-tag-name guesses -- arrivalSequence/sequenceElement/meterData/
acid/flightId/meterFix/facility/artcc, etc. -- never matched anything and are
why tbfm_sequences was 0 rows all session; this is the corrected version,
based on 5 real captured messages from the ZAU (Chicago Center) TMA feed):

  <env xmlns="urn:us:gov:dot:faa:atm:tfm:tbfmmeteringpublication:1.1.0"
       envTime="..." envSrce="TMA.ZAU.FAA.GOV">
    <tma msgTime="..." msgId="...">
      <air gufi="..." tmaId="..." apt="ORD" dap="CYYC" aid="AAL1633" airType="AMD">
        <!-- EITHER a flight-plan update (no metering data -- skip these): -->
        <flt><aid>AAL1633</aid><dap>CYYC</dap><apt>ORD</apt></flt>
        <!-- OR a metering/ETA update (this is what we want): -->
        <eta><mfx>MOTRR</mfx><eta_mfx>2026-07-20T15:28:12Z</eta_mfx>
             <eta_rwy>2026-07-20T15:47:39Z</eta_rwy></eta>
      </air>
    </tma>
  </env>

Field mapping:
  <env envSrce="TMA.{ARTCC}.FAA.GOV">  -> facility = {ARTCC} (e.g. "ZDC")
  <air apt="...">                      -> metered/arrival airport (3-letter,
                                           e.g. "DCA" -- NOT "KDCA"; TBFM uses
                                           3-letter airport codes, not ICAO)
  <air dap="...">                      -> departure airport (3-letter)
  <air aid="...">                      -> flight callsign/ident (e.g. "AAL1633")
  <air gufi="...">                     -> global flight ID
  <eta><mfx>                           -> meter fix name
  <eta><eta_mfx> / <eta_rwy> /
       <eta_dfx> / <eta_sfx>           -> ETA at various arcs; prefer runway,
                                           then meter fix, then descent/speed
                                           fix (whichever is present)
  <flt>-only <air> elements (no <eta>) -> flight-plan updates, not metering
                                           data -- correctly produce no
                                           sequence record (no ETA to store)

Filtering to DC-area now happens primarily on <air apt="..."> being one of
DCA/IAD/BWI directly (a direct semantic match), with facility=="ZDC" kept as
a secondary/logged expectation rather than a hard requirement -- ZDC should
be the ARTCC publishing DCA/IAD/BWI metering in practice, but filtering on
the airport code itself is more robust than inferring relevance from which
ARTCC happened to publish it.
"""
from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from common import db
from common.push_dedup import PushDedup, content_hash
from shared.watchlist import _fire_ntfy_dual

log = logging.getLogger("ingest.parsers.tbfm")

_TBFM_ALERT_DEDUP = PushDedup("tbfm_alerts", dedup_secs=300)

# DC-area meter fixes — kept for alert labeling / cross-checks, no longer the
# primary relevance filter (see module docstring -- <air apt="..."> is now
# the primary filter).
#
# 2026-09-02 (operator directive, backfilled against real FAA data): values
# are (lat, lon) in decimal degrees where known, None where not. Checked
# BOTH the NASR 28-Day Subscription FIX file (national reporting-point
# database, cycle effective 2026-08-06) AND the CIFP (Coded Instrument
# Flight Procedures, ARINC 424, same cycle) -- the authoritative source for
# procedure-only RNAV waypoints that never appear in the general FIX file.
# PALEO/RAVNN/SWANN/WOOLY/FLUKY confirmed in the NASR FIX file, each
# independently cross-checked against its ARTCC field (all ZDC = Washington
# Center, consistent with real DC-area metering). LUCIT/MERIT/JIMBO/WAVER/
# SFARA do NOT exist as DC-area records in either source: each of LUCIT/
# MERIT/JIMBO/WAVER matched exactly one national FIX/CIFP record under that
# exact identifier, and every single one was somewhere else entirely
# (Indiana/ZAU, Connecticut/ZBW, Oklahoma/ZKC military, Texas/ZHU) -- not a
# lookup miss, a genuine same-name-different-place collision. SFARA had no
# match in either source at all. Conclusion: these 5 are almost certainly
# TBFM-internal automation labels, not charted/public navigation fixes --
# geolocating them would need a fundamentally different approach (e.g.
# inferring position empirically from many live TBFM ETA/sequence messages
# cross-referenced against real radar tracks), not another data lookup.
DC_METER_FIXES: dict[str, tuple[float, float] | None] = {
    "LUCIT": None,                                  # IAD -- unresolved, see note above
    "SWANN": (39.151467, -76.228872),                # IAD
    "RAVNN": (38.778628, -76.577564),                # IAD
    "FLUKY": (38.506444, -77.729222),                # IAD
    "SFARA": None,                                   # IAD -- unresolved, see note above
    "JIMBO": None,                                   # DCA -- unresolved, see note above
    "WAVER": None,                                   # DCA -- unresolved, see note above
    "WOOLY": (39.338661, -77.036436),                # DCA
    "PALEO": (39.027994, -76.372725),                # BWI
    "MERIT": None,                                   # BWI -- unresolved, see note above
}

# TBFM's <air apt="..."> uses 3-letter airport codes, not ICAO.
DC_AREA_APTS = frozenset({"DCA", "IAD", "BWI"})

_ENV_SRCE_RE = re.compile(r"^TMA\.([A-Z0-9]+)\.FAA\.GOV$", re.IGNORECASE)

_DEBUG_SAMPLE_DIR = "/var/lib/corporatetraveldc/tbfm_debug"
_DEBUG_SAMPLE_MAX = 5
_debug_sample_count = 0


def _maybe_capture_debug_sample(xml_bytes: bytes) -> None:
    """
    One-shot full-message capture used to confirm the real schema on
    2026-07-20 (see module docstring). Left in place, self-limited to
    _DEBUG_SAMPLE_MAX writes for the life of the process, in case the
    schema needs re-confirming after a future FAA-side change.
    """
    global _debug_sample_count
    if _debug_sample_count >= _DEBUG_SAMPLE_MAX:
        return
    try:
        os.makedirs(_DEBUG_SAMPLE_DIR, exist_ok=True)
        path = f"{_DEBUG_SAMPLE_DIR}/sample_{_debug_sample_count}.xml"
        with open(path, "wb") as f:
            f.write(xml_bytes)
        _debug_sample_count += 1
        log.info("tbfm: wrote debug sample %s (%d bytes)", path, len(xml_bytes))
    except Exception as e:
        log.warning("tbfm: debug sample capture failed: %s", e)


# Unknown <air>-child capture -- added 2026-08-30 (SWIM audit), mirroring
# tfms_parser's unknown-msgType bypass capture that surfaced GDP/GS/AFP/
# FADT one by one. Context: an external SWIM consumer's document claims
# TBFM also publishes STA schedule messages (an <sta> sibling to the
# <eta>/<flt> children we parse, carrying sta_rwy/sta_mfx/... -- runway
# schedule times, i.e. the metering delay when diffed against eta_rwy).
# NOTHING in this box's real captures confirms that: all 10 messages ever
# captured here (5 on 2026-07-20, 5 on 2026-08-30) carry only <flt> and
# <eta> children, and this feed's handler never reads the Solace topic
# string, so whether our FAA-provisioned queue subscription even includes
# a schedule topic kind is genuinely unknown from this side. Per this
# repo's own repeatedly-proven rule (tbfm/tfms/smes tag-guess history:
# every schema guessed without a captured sample was wrong), we do NOT
# parse <sta> speculatively -- this capture answers the question
# empirically instead: if an <air> child with any tag other than flt/eta
# ever arrives, its full message lands here (per-tag capped, same pattern
# as smes_parser's tag-keyed capture) and the STA claim can be settled
# against a real sample before a single parsing line is written.
_KNOWN_AIR_CHILD_TAGS = frozenset({"flt", "eta"})
_UNKNOWN_KIND_DIR = "/var/lib/corporatetraveldc/tbfm_debug_unknown_kind"
_UNKNOWN_KIND_MAX_PER_TAG = 5
_unknown_kind_counts: dict[str, int] = {}


def _maybe_capture_unknown_air_kind(xml_bytes: bytes, tag: str) -> None:
    count = _unknown_kind_counts.get(tag, 0)
    if count >= _UNKNOWN_KIND_MAX_PER_TAG:
        return
    try:
        safe_tag = "".join(c if c.isalnum() else "_" for c in tag)[:40] or "unknown"
        os.makedirs(_UNKNOWN_KIND_DIR, exist_ok=True)
        path = f"{_UNKNOWN_KIND_DIR}/{safe_tag}_{count}.xml"
        with open(path, "wb") as f:
            f.write(xml_bytes)
        log.info("tbfm: captured unknown <air> child kind %r -> %s (%d bytes)",
                 tag, path, len(xml_bytes))
    except Exception as e:
        log.warning("tbfm: unknown-air-kind capture failed for %r: %s", tag, e)
    _unknown_kind_counts[tag] = count + 1


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _child_text(elem: ET.Element | None, tag: str) -> str | None:
    """Direct-child text lookup, namespace-agnostic (env's default namespace
    means ElementTree tags come through as '{uri}tag' -- match on local name)."""
    if elem is None:
        return None
    for child in elem:
        if _local(child.tag) == tag:
            return (child.text or "").strip() or None
    return None


def _facility_from_env_srce(root: ET.Element) -> str | None:
    """Extract ARTCC code from <env envSrce="TMA.ZDC.FAA.GOV">, e.g. 'ZDC'."""
    srce = root.get("envSrce") or ""
    m = _ENV_SRCE_RE.match(srce.strip())
    return m.group(1).upper() if m else None


def _parse_eta_timestamp(ts: str | None) -> str | None:
    if not ts:
        return None
    ts = ts.strip()
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        pass
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M"):
        try:
            dt = datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return ts  # return as-is if unparseable


def _parse_air_element(air: ET.Element, facility: str | None) -> dict | None:
    """
    Parse one <air> element. Returns a sequence dict only if it carries an
    <eta> block (meter fix + timing) -- <flt>-only elements are flight-plan
    updates with no metering data and correctly produce nothing.
    """
    apt = (air.get("apt") or "").upper() or None
    if apt not in DC_AREA_APTS:
        return None

    eta_elem = None
    for child in air:
        if _local(child.tag) == "eta":
            eta_elem = child
            break
    if eta_elem is None:
        return None  # flight-plan-only <flt> update, not a metering record

    meter_fix = _child_text(eta_elem, "mfx")
    if not meter_fix:
        return None

    flight_id = air.get("aid") or _child_text(air, "aid")
    if not flight_id:
        return None

    # Prefer runway ETA, then meter-fix ETA, then descent/speed-fix ETAs
    # (whichever arc this particular message happens to carry).
    eta_raw = (
        _child_text(eta_elem, "eta_rwy")
        or _child_text(eta_elem, "eta_mfx")
        or _child_text(eta_elem, "eta_dfx")
        or _child_text(eta_elem, "eta_sfx")
    )

    return {
        "meter_fix":      meter_fix.upper(),
        "facility":       facility or "ZDC",
        "flight_id":      flight_id.upper(),
        "eta":            _parse_eta_timestamp(eta_raw) or "",
        "sequence_num":   None,  # not present in this message schema
        "assigned_speed": None,  # not present in this message schema
        "apt":            apt,
        "dap":            (air.get("dap") or "").upper() or None,
    }


def parse_tbfm_message(xml_bytes: bytes) -> list[dict]:
    """
    Parse a TBFM NMS XML message (env > tma > air[> eta|flt]).
    Returns a list of sequence dicts for DC-area (<air apt="DCA|IAD|BWI">)
    metering updates only -- flight-plan-only <flt> messages and non-DC-area
    airports are filtered out.
    """
    if not xml_bytes:
        return []
    _maybe_capture_debug_sample(xml_bytes)
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("tbfm: XML parse error: %s", e)
        return []

    facility = _facility_from_env_srce(root)

    sequences: list[dict] = []
    for elem in root.iter():
        if _local(elem.tag) == "air":
            # 2026-08-30 (SWIM audit): capture any <air> child shape we
            # don't parse -- settles empirically whether this queue ever
            # delivers the claimed <sta> schedule family (or anything
            # else). See _maybe_capture_unknown_air_kind's comment block.
            for child in elem:
                child_tag = _local(child.tag)
                if child_tag not in _KNOWN_AIR_CHILD_TAGS:
                    _maybe_capture_unknown_air_kind(xml_bytes, child_tag)
            seq = _parse_air_element(elem, facility)
            if seq:
                sequences.append(seq)

    if not sequences and xml_bytes:
        log.debug("tbfm: no DC-area sequences in message (facility=%s); raw prefix: %s",
                   facility, xml_bytes[:300].decode("utf-8", errors="replace"))

    return sequences


# Minimum aircraft-in-sequence count for a meter fix to be considered real
# congestion -- added 2026-08-02 per operator direction. Before this,
# check_tbfm_alerts() fired for every fix with ANY sequence data at all,
# down to a single aircraft, which is normal/routine metering, not
# congestion worth a push. Operator's own framing: "at least five aircraft
# in sequence, or legitimate congestion" -- interpreted as a single
# threshold (five aircraft IS the bar for "legitimate congestion" here,
# not a second independent signal) since seq_count is the only reliable
# congestion proxy available in this feed; there's no separate hold/delay
# field to check against. Flag to the operator if a second, ETA-spread-
# based signal turns out to be wanted in addition to this.
_MIN_SEQ_FOR_ALERT = 5


def check_tbfm_alerts(sequences: list[dict]) -> None:
    """
    Fire tbfm-alerts ntfy for DC-area meter fix sequencing updates.

    UPDATED 2026-07-21 per operator direction: moved off nas-alerts onto
    its own tbfm-alerts topic (metering is a distinct concern from general
    NAS/NOTAM traffic), and additionally fires a second, identical push to
    a per-ARTCC topic (tbfm-zny/tbfm-zdc/etc, see shared.sector_coalesce)
    when the fix's facility is one of the 8 sectors the operator wants to
    track in isolation -- so a sector-specific subscription shows only
    that sector's metering load/congestion trend, while tbfm-alerts still
    carries everything nationwide for the aggregate view.

    UPDATED 2026-08-02 per operator direction: gated on _MIN_SEQ_FOR_ALERT
    (5) -- a single aircraft (or a handful under the threshold) at a meter
    fix is routine, not congestion, and was blasting a push for every
    trivial update. Sub-threshold fixes are skipped before the dedup check
    even runs, so they don't consume/reset that fix's dedup window either.

    UPDATED 2026-08-02 (later same day) per operator direction: switched
    from a manual dual-fire (always push to tbfm-alerts + push to the zone
    topic if resolved) to shared.sector_coalesce.fire_family_alert(), which
    adds real escalation gating on top of the _MIN_SEQ_FOR_ALERT floor --
    "keep TBFM alerts... specifically for the escalating metering flight
    plan issues" -- so tbfm-alerts and tbfm-<zone> now only fire when a
    fix's activity is genuinely trending up vs. the last 15 minutes, not
    on every single qualifying (5+ aircraft) update. Per-zone sensitivity
    is independently tunable via
    shared.sector_coalesce.set_escalate_threshold("tbfm", "<ZONE_NAME>",
    multiplier, floor) -- note ZONE_NAME here is the _SECTOR_FACILITY_MAP
    name (e.g. "DC_LOCAL", "NEW_YORK"), not the ntfy topic code (zdc/zny).
    """
    from shared.sector_coalesce import fire_family_alert

    by_fix: dict[str, list[dict]] = {}
    for s in sequences:
        fix = s.get("meter_fix", "").upper()
        by_fix.setdefault(fix, []).append(s)

    for fix, fix_seqs in by_fix.items():
        # 2026-08-30 (real bug found live): this used to be len(fix_seqs)
        # -- the count of flights in THIS single incoming SWIM message,
        # not the fix's actual current queue. TBFM sends incremental
        # per-flight updates, so a real 40+-aircraft queue (confirmed
        # live: ZDC had 57, DC_MET 43 in tbfm_sequences) almost never
        # produces a single-message batch anywhere near
        # _MIN_SEQ_FOR_ALERT, permanently suppressing every TBFM alert
        # regardless of real congestion -- exactly what the operator
        # observed (TBFM ingest healthy and writing data, zero alerts
        # ever reaching ntfy). See db.get_active_tbfm_sequence_count()'s
        # own docstring for the full writeup.
        seq_count = db.get_active_tbfm_sequence_count(fix)
        if seq_count < _MIN_SEQ_FOR_ALERT:
            continue
        dedup_key = content_hash(f"tbfm:{fix}:{seq_count}")
        # Bug fixed 2026-07-21: dedup slot key was the constant string "tbfm"
        # for every meter fix, so with ~100 fixes active nationwide on this
        # feed, each fix's check evicted every other fix's dedup state --
        # confirmed live via two fixes (IIU, CAVLR) re-firing ~10s apart,
        # far inside the 300s window, because an unrelated fix's alert had
        # overwritten the single shared slot in between. Per-fix key fixes it.
        dedup_slot = f"tbfm:{fix}"
        if not _TBFM_ALERT_DEDUP.should_push(dedup_slot, dedup_key):
            continue

        eta_info = ""
        for s in fix_seqs:
            if s.get("eta"):
                eta_info = f" | lead ETA {s['eta']}"
                break

        # Representative facility for this fix -- majority vote across its
        # sequences, falling back to the first non-empty value. In practice
        # a given meter fix belongs to one ARTCC, so this is almost always
        # unanimous; the majority vote just avoids picking an outlier if a
        # feed glitch ever tags one sequence with a different facility.
        facilities = [s.get("facility") for s in fix_seqs if s.get("facility")]
        facility = max(set(facilities), key=facilities.count) if facilities else None

        title = f"TBFM Metering — {fix}"
        detail = f"{fix}: {seq_count} aircraft in sequence{eta_info}"
        dispatch = f"{fix}: {seq_count} in sequence"
        try:
            result = fire_family_alert("tbfm", "tbfm", facility, title, detail, dispatch, base_priority=2)
            _TBFM_ALERT_DEDUP.record(dedup_slot, dedup_key)
            log.info("tbfm: fire_family_alert for fix %s (%d in seq, facility=%s, sector=%s, "
                      "escalating=%s, aggregate_fired=%s, zone_fired=%s)",
                      fix, seq_count, facility, result.get("sector"), result.get("escalating"),
                      result.get("fired"), result.get("zone_fired"))
        except Exception as e:
            log.error("tbfm: tbfm-alert fire failed for %s: %s", fix, e)


def _match_watchlist_flight(identifier: str | None) -> dict | None:
    """Find an active flight watchlist entry matching this identifier
    (callsign), case-insensitive. Same matching convention as
    tfms_parser.py's own copy (kept file-local rather than shared -- each
    ingest parser already does this, see that copy's docstring)."""
    if not identifier:
        return None
    try:
        from shared.watchlist import get_active_entries
        entries = get_active_entries(entry_type="flight")
    except Exception as e:
        log.error("tbfm: watchlist lookup failed: %s", e)
        return None
    ident_upper = identifier.upper().strip()
    for entry in entries:
        if entry["identifier"].upper() == ident_upper:
            return entry
    return None


_TBFM_WATCHLIST_DEDUP = PushDedup("tbfm_watchlist", dedup_secs=1800)


def _check_tbfm_watchlist_hits(sequences: list[dict]) -> None:
    """Per-flight TBFM arrival-sequencing update for any watched flight.

    2026-08-28 (operator directive: "let TBFM also be in the authority
    chain"): before this, TBFM had ZERO connection to individual watchlist
    entries -- check_tbfm_alerts() above only ever fires an AGGREGATE
    nationwide/per-sector congestion alert (5+ aircraft at one fix), never
    a per-flight one, so a watched flight being actively sequenced for
    arrival into DCA/IAD/BWI was invisible to its own watchlist entry no
    matter how much TBFM data existed for it.

    Does NOT force this into oooi_phase (TBFM has no literal OUT/OFF/ON/IN
    timestamp, only a meter-fix/runway crossing ETA -- forcing an
    inaccurate phase transition would be worse than not having one) --
    uses its own dedicated column instead (update_watchlist_tbfm_status),
    the same shared-field-bug-avoidance pattern already established for
    FDPS/FIDS (see common/db.py SCHEMA_V23's history).

    30-min dedup per (entry, meter fix) -- TBFM re-sends the same
    sequence/ETA on a short cadence while a flight sits in the metering
    queue; this fires once per fix per flight, not on every re-send,
    matching _handle_track_information's TFMS-side 30-min approach-alert
    cadence in tfms_parser.py.
    """
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for s in sequences:
        entry = _match_watchlist_flight(s.get("flight_id"))
        if entry is None:
            continue
        fix = s.get("meter_fix") or "?"
        eta = s.get("eta") or ""
        apt = s.get("apt") or "?"
        status = f"{apt} arrival, meter fix {fix}" + (f", ETA {eta}" if eta else "")

        dedup_key = content_hash(f"tbfm:{entry['id']}:{fix}")
        if not _TBFM_WATCHLIST_DEDUP.should_push(entry["id"], dedup_key):
            continue

        try:
            db.update_watchlist_tbfm_status(entry["id"], status, now_iso)
        except Exception as e:
            log.error("tbfm: update_watchlist_tbfm_status failed for %s: %s", entry["id"], e)

        summary = f"{s.get('flight_id')} TBFM: {status}"
        try:
            from shared.watchlist import watchlist_event_hit
            watchlist_event_hit(
                entry["id"], summary,
                {"watchlist_trigger": "tbfm_sequenced", "meter_fix": fix,
                 "eta": eta, "airport": apt, "facility": s.get("facility")},
                priority=3,
            )
        except Exception as e:
            log.error("tbfm: watchlist_event_hit failed for %s: %s", entry["id"], e)

        _TBFM_WATCHLIST_DEDUP.record(entry["id"], dedup_key)


def write_tbfm_sequences(sequences: list[dict]) -> int:
    """Upsert TBFM sequences into tbfm_sequences table. Returns count written."""
    written = 0
    for s in sequences:
        if not s.get("eta"):
            continue
        try:
            db.upsert_tbfm_sequence(
                meter_fix=s["meter_fix"],
                facility=s["facility"],
                flight_id=s["flight_id"],
                eta=s["eta"],
                sequence_num=s.get("sequence_num"),
                assigned_speed=s.get("assigned_speed"),
            )
            written += 1
        except Exception as e:
            log.error("tbfm: db write error for %s@%s: %s",
                      s.get("flight_id"), s.get("meter_fix"), e)
    if sequences:
        check_tbfm_alerts(sequences)
        _check_tbfm_watchlist_hits(sequences)
    return written
