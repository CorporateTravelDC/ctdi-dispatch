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
DC_METER_FIXES = frozenset({
    "LUCIT", "SWANN", "RAVNN", "FLUKY", "SFARA",   # IAD
    "JIMBO", "WAVER", "WOOLY",                       # DCA
    "PALEO", "MERIT",                                # BWI
})

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
            seq = _parse_air_element(elem, facility)
            if seq:
                sequences.append(seq)

    if not sequences and xml_bytes:
        log.debug("tbfm: no DC-area sequences in message (facility=%s); raw prefix: %s",
                   facility, xml_bytes[:300].decode("utf-8", errors="replace"))

    return sequences


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
    """
    from shared.sector_coalesce import sector_ntfy_topic

    by_fix: dict[str, list[dict]] = {}
    for s in sequences:
        fix = s.get("meter_fix", "").upper()
        by_fix.setdefault(fix, []).append(s)

    for fix, fix_seqs in by_fix.items():
        seq_count = len(fix_seqs)
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
            _fire_ntfy_dual("tbfm-alerts", title, detail, dispatch, priority=2)
            sector_topic = sector_ntfy_topic(facility)
            if sector_topic:
                _fire_ntfy_dual(sector_topic, title, detail, dispatch, priority=2)
            _TBFM_ALERT_DEDUP.record(dedup_slot, dedup_key)
            log.info("tbfm: tbfm-alert fired for fix %s (%d in seq, facility=%s, sector_topic=%s)",
                      fix, seq_count, facility, sector_topic)
        except Exception as e:
            log.error("tbfm: tbfm-alert fire failed for %s: %s", fix, e)


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
    return written
