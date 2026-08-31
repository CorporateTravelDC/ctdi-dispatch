"""
ingest.parsers.itws_parser — FAA ITWS (Integrated Terminal Weather System) NMS parser.

ITWS delivers processed terminal weather products for DCA, IAD, and BWI:
  - PRECIP     : precipitation type, rate, forecast (0-60 min)
  - WIND_SHEAR : wind shear alerts at runway thresholds
  - MICROBURST : microburst alerts (short-duration wind shear, high severity)
  - LIGHTNING  : lightning strike counts and proximity alerts
  - CEILING    : terminal ceiling and visibility reports (METARs already cover this
                 but ITWS adds forecast confidence)

ITWS data is NOT available via REST — NMS is the only source. It augments rather
than replaces the METAR REST feed. Heartbeat key: "itws".

Severity scale (FAA ITWS convention):
  1-2 = light, 3-4 = moderate, 5-6 = severe

--- REAL SCHEMA, confirmed 2026-07-20 ---
Root is a flat, non-namespaced structure, nothing like the guessed
itwsProduct/weatherAlert/precipAlert/etc tags:

  <itws_msg>
    <packet_header>
      <packet_header_msgno>...</packet_header_msgno>
      <packet_header_product>...</packet_header_product>
    </packet_header>
    <product_header>
      <product_header_msg_id>
        <product_msg_id>...</product_msg_id>
        <product_msg_name>Microburst ATIS Product</product_msg_name>  <- key routing field
      </product_header_msg_id>
      <product_header_airports>ORD</product_header_airports>
      <product_header_source_id>ORD</product_header_source_id>
      ...
    </product_header>
    <!-- body varies entirely by product_msg_name, see taxonomy below -->
  </itws_msg>

Confirmed real product_msg_name values across two sampling rounds (25+
distinct products, not the 5-value PRECIP/WIND_SHEAR/MICROBURST/LIGHTNING/
CEILING taxonomy originally guessed):

  Likely-simple, alert-style (small payloads, ~3KB, probably a normal-
  effort parse once field names inside the body are confirmed):
    Microburst ATIS Product, Wind Shear ATIS Product,
    Tornado Alert Product, Tornado Detections Product,
    Terminal Weather Text Normal Product, Hazard Text Long Range Product,
    SM SEP Long Range Product, SM SEP 5nm Product,
    Configured Alerts Product, AP Status

  Heavy / raster-style (large payloads, several KB to 600+KB, carry
  gridded/compressed data -- e.g. Precipitation Long Range Product's
  920x920 run-length-encoded radar grid): STASH candidates, need real
  decode work, not a quick tag mapping:
    Precipitation Long Range Product, Precipitation 5nm Product,
    AP Indicated Precipitation Product, Forecast Image Product (up to
    645KB observed), Forecast Contour Product (up to 108KB observed),
    Forecast Accuracy Product

This is meaningfully more heterogeneous than TFMS turned out to be (no
single RSTR-style quick win found) -- 13+ distinct product shapes with no
common body structure, several of them large binary-ish raster payloads.
Scoped as its own multi-product rewrite for the dedicated session: sort
by simple-vs-heavy first, implement the simple alert-style ones, stash
the raster ones same as FDPS's 4.2 legacy and TFMS's APTC/GADV.
"""
from __future__ import annotations

import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from common import db
from common.push_dedup import PushDedup, content_hash
from shared.watchlist import _fire_ntfy_dual  # reuse ntfy infra for ITWS alerts

log = logging.getLogger("ingest.parsers.itws")

# One-shot full-message debug capture -- 2026-07-20, same technique that
# confirmed tbfm_parser.py's real schema. This parser's tag guesses
# (itwsProduct/weatherAlert/precipAlert/windShearAlert/etc.) have never been
# validated against a real captured message -- the only visibility so far is
# a 300-byte raw-prefix log when nothing parses, which isn't enough to map a
# real schema. Capture is self-limited to _DEBUG_SAMPLE_MAX writes for the
# life of the process; cap is higher than tbfm/tfms since ITWS only covers
# 3 airports and terminal-weather products may be much less frequent.
_DEBUG_SAMPLE_DIR = "/var/lib/corporatetraveldc/itws_debug"
_DEBUG_SAMPLE_MAX = 15
_debug_sample_count = 0


def _maybe_capture_debug_sample(xml_bytes: bytes) -> None:
    global _debug_sample_count
    if _debug_sample_count >= _DEBUG_SAMPLE_MAX:
        return
    try:
        os.makedirs(_DEBUG_SAMPLE_DIR, exist_ok=True)
        path = f"{_DEBUG_SAMPLE_DIR}/sample_{_debug_sample_count}.xml"
        with open(path, "wb") as f:
            f.write(xml_bytes)
        _debug_sample_count += 1
        log.info("itws: wrote debug sample %s (%d bytes)", path, len(xml_bytes))
    except Exception as e:
        log.warning("itws: debug sample capture failed: %s", e)


# Parse-failure capture -- 2026-07-20, added after a real (non-fatal, caught)
# "not well-formed (invalid token)" error was seen post-sanitize on a live
# message. None of the 15 generic samples reproduced it (cap was already
# full by the time that message arrived), so this bypasses the generic cap
# entirely and captures ANY message that fails to parse, unconditionally,
# to catch the exact bytes next time it recurs.
_PARSE_FAILURE_DIR = "/var/lib/corporatetraveldc/itws_debug_parsefail"
_PARSE_FAILURE_MAX = 10
_parse_failure_count = 0


def _capture_parse_failure_sample(xml_bytes: bytes, error: str) -> None:
    global _parse_failure_count
    if _parse_failure_count >= _PARSE_FAILURE_MAX:
        return
    try:
        os.makedirs(_PARSE_FAILURE_DIR, exist_ok=True)
        path = f"{_PARSE_FAILURE_DIR}/failure_{_parse_failure_count}.xml"
        with open(path, "wb") as f:
            f.write(xml_bytes)
        with open(f"{path}.error.txt", "w") as f:
            f.write(error)
        _parse_failure_count += 1
        log.info("itws: wrote parse-failure sample %s (%d bytes, error=%s)",
                  path, len(xml_bytes), error)
    except Exception as e:
        log.warning("itws: parse-failure sample capture failed: %s", e)

_ITWS_NS = {
    "itws": "http://www.faa.aero/itws/1.0",
    "wx":   "http://www.faa.aero/wx/1.0",
}

# DC-area site codes only -- ITWS turned out to be a NATIONWIDE feed (real
# captures show CLE, SLC, etc. in product_header_airports), not scoped to
# DCA/IAD/BWI as originally assumed. All three DC-area sites happen to have
# 3-letter IATA codes identical to their ICAO minus the K (DCA, IAD, BWI),
# so plain 3-letter equality works without needing a lookup table.
ITWS_AIRPORTS = frozenset({"KDCA", "KIAD", "KBWI"})

# Severity threshold above which we fire an ntfy alert (used by the legacy
# alert-shaped path below; per-product handlers mostly decide their own
# fire/no-fire condition directly, since ITWS severity isn't a single
# consistent field across product types the way it was originally guessed).
ITWS_ALERT_SEVERITY = 4

_PRODUCT_TYPES = frozenset({
    "PRECIP", "WIND_SHEAR", "MICROBURST", "LIGHTNING", "CEILING",
    # Aliases in some ITWS schemas
    "PRECIPITATION", "WINDSHEAR", "MICRO_BURST",
})

_PRODUCT_CANONICAL = {
    "PRECIPITATION": "PRECIP",
    "WINDSHEAR": "WIND_SHEAR",
    "MICRO_BURST": "MICROBURST",
}

# ── Real product dispatcher, 2026-07-20 3pm session ─────────────────────────
# Confirmed real root: itws_msg > product_header > product_header_msg_id >
# product_msg_name (routing key) + product_header_airports (3-letter site).
# 15 real captures so far, all "quiet" baseline states (no active hazard in
# any sample) -- field names below are confirmed structurally, but the
# ACTIVE/non-zero shape of each product has not been directly observed yet.
# Trigger conditions are inferred from field naming (count>0, non-OFF status,
# non-negative ETI) rather than confirmed against a real positive case.

# One sample captured per never-before-seen product_msg_name, bypassing the
# generic _DEBUG_SAMPLE_MAX cap -- lets new product variants get captured
# for later analysis without needing another manual debugging pass.
_PRODUCT_SAMPLE_DIR = "/var/lib/corporatetraveldc/itws_debug_by_product"
_PRODUCT_SAMPLE_TOTAL_MAX = 60
_seen_product_names: set[str] = set()
_product_sample_count = 0


def _maybe_capture_product_sample(xml_bytes: bytes, product_name: str) -> None:
    global _product_sample_count
    if product_name in _seen_product_names or _product_sample_count >= _PRODUCT_SAMPLE_TOTAL_MAX:
        return
    try:
        os.makedirs(_PRODUCT_SAMPLE_DIR, exist_ok=True)
        safe_name = "".join(c if c.isalnum() else "_" for c in product_name)[:60]
        path = f"{_PRODUCT_SAMPLE_DIR}/{safe_name}.xml"
        with open(path, "wb") as f:
            f.write(xml_bytes)
        _seen_product_names.add(product_name)
        _product_sample_count += 1
        log.info("itws: wrote first-seen sample for product %r -> %s", product_name, path)
    except Exception as e:
        log.warning("itws: product sample capture failed: %s", e)


def _find30(elem: ET.Element | None, tag: str) -> ET.Element | None:
    """First direct child matching local tag name (ITWS body is unnamespaced)."""
    if elem is None:
        return None
    return elem.find(tag)


def _child_text(elem: ET.Element | None, tag: str) -> str | None:
    child = _find30(elem, tag)
    return (child.text or "").strip() or None if child is not None and child.text else None


def _norm_airport(code: str | None) -> str | None:
    if not code:
        return None
    code = code.upper().strip()
    if len(code) == 3:
        code = "K" + code
    return code


def _handle_atis_pmsg(body: ET.Element, label: str) -> tuple[int, str] | None:
    """atis_pmsg: pmsg_status (OFF when quiet), pmsg_timer (minutes).
    Shared by Microburst ATIS and Wind Shear ATIS -- same body shape,
    confirmed for Microburst directly, inferred for Wind Shear by product
    family naming convention (not yet directly sampled)."""
    pmsg = _find30(body, "atis_pmsg")
    if pmsg is None:
        return None
    status = _child_text(pmsg, "pmsg_status") or "OFF"
    timer = _child_text(pmsg, "pmsg_timer") or "0"
    if status.upper() == "OFF":
        return (0, f"{label}: OFF")
    return (5, f"{label}: {status} ({timer} min)")


def _handle_microburst_atis(body: ET.Element) -> tuple[int, str] | None:
    return _handle_atis_pmsg(body, "Microburst ATIS")


def _handle_wind_shear_atis(body: ET.Element) -> tuple[int, str] | None:
    return _handle_atis_pmsg(body, "Wind Shear ATIS")


def _handle_hazard_text(body: ET.Element) -> tuple[int, str] | None:
    """haz_text: ht_num_cells (0 = clear). Real per-cell hazard detail
    fields not observed yet (all captures had count=0) -- only the count
    is used for now."""
    haz = _find30(body, "haz_text")
    if haz is None:
        return None
    n_str = _child_text(haz, "ht_num_cells") or "0"
    try:
        n = int(n_str)
    except ValueError:
        n = 0
    if n <= 0:
        return (0, "Hazard text: no active cells")
    return (4, f"Hazard text: {n} active hazard cell(s)")


def _handle_sm_sep(body: ET.Element) -> tuple[int, str] | None:
    """sm_sep: sm_num_storms (0 = clear), sm_latitude/sm_longitude/sm_rotation."""
    sm = _find30(body, "sm_sep")
    if sm is None:
        return None
    n_str = _child_text(sm, "sm_num_storms") or "0"
    try:
        n = int(n_str)
    except ValueError:
        n = 0
    if n <= 0:
        return (0, "Storm cells: none tracked")
    lat = _child_text(sm, "sm_latitude") or "?"
    lon = _child_text(sm, "sm_longitude") or "?"
    return (4, f"Storm cells: {n} tracked near {lat},{lon}")


def _handle_ap_status(body: ET.Element) -> tuple[int, str] | None:
    """ap_status: aps_radar/apsr_data[*]/apsd_results (0 = no precip
    detected at that reflectivity threshold)."""
    ap = _find30(body, "ap_status")
    if ap is None:
        return None
    max_results = 0
    for radar in ap.findall("aps_radar"):
        for data in radar.findall("apsr_data"):
            r_str = _child_text(data, "apsd_results") or "0"
            try:
                r = int(r_str)
            except ValueError:
                r = 0
            max_results = max(max_results, r)
    if max_results <= 0:
        return (0, "AP precip status: clear")
    return (3, f"AP precip status: results={max_results}")


def _handle_gust_front_eti(body: ET.Element) -> tuple[int, str] | None:
    """gf_eti: gf_eti_near (bool), gf_eti_minutes (-1 = none pending),
    gf_eti_horizon (lookout window, minutes)."""
    gf = _find30(body, "gf_eti")
    if gf is None:
        return None
    near = (_child_text(gf, "gf_eti_near") or "0") == "1"
    mins_str = _child_text(gf, "gf_eti_minutes") or "-1"
    try:
        mins = int(mins_str)
    except ValueError:
        mins = -1
    if not near or mins < 0:
        return (0, "Gust front: none pending")
    return (4, f"Gust front: ETI {mins} min")


def _handle_terminal_weather_graphics(body: ET.Element) -> tuple[int, str] | None:
    """twp_graphics_product -- IMPLEMENTED 2026-07-21. Confirmed real
    structure via itws_debug_by_product/Terminal_Weather_Graphics_Product.xml:
        twp_graphics_product > twp_airport, twp_time, twp_map_range,
                                twp_legend, twp_graphics (free-text summary,
                                e.g. "NO STORMS WITHIN 15NM" when quiet)
    ITWS renders its own natural-language summary line here rather than a
    structured count/threshold field. "NO STORMS" prefix is the only
    confirmed quiet state; anything else is surfaced verbatim at moderate
    severity rather than further parsed, since no active-state sample has
    been observed yet.
    """
    twp = _find30(body, "twp_graphics_product")
    if twp is None:
        return None
    graphics_text = _child_text(twp, "twp_graphics") or ""
    graphics_text = " ".join(graphics_text.split())
    if not graphics_text:
        return None
    if graphics_text.upper().startswith("NO STORMS"):
        return (0, f"Terminal weather graphics: {graphics_text}")
    return (3, f"Terminal weather graphics: {graphics_text}")


def _handle_runway_configuration(body: ET.Element) -> tuple[int, str] | None:
    """Runway Configuration Product -- IMPLEMENTED 2026-08-30 (SWIM audit
    blind sweep). Confirmed real structure via
    itws_debug_by_product/Runway_Configuration_Product.xml (captured
    2026-08-30, PCT site, airport IAD):
        rwy_config > rc_ap_id (airport), rc_config_name (e.g.
                     "IAD-19L-19C-12" -- the ACTIVE runway configuration),
                     rc_rbdt_location > rc_rbdt > rc_rbdt_line* (ribbon
                     display line status -- not extracted, display detail)
    This product was hitting the unrecognized-product log path and being
    dropped, despite being exactly the "which runways are active right
    now" signal several other consumers here want (per-runway RVR
    preference, arrival-runway ETA context). Stored as a severity-0
    current-state row (itws_alerts is keyed (airport, product_type), so
    the latest config is always queryable); never fires a push on its own
    -- an operator alert on config CHANGE is deliberately left for a later
    pass once enough history confirms how often configs legitimately flip.
    """
    rc = _find30(body, "rwy_config")
    if rc is None:
        return None
    config_name = _child_text(rc, "rc_config_name")
    if not config_name:
        return None
    return (0, f"Active runway configuration: {config_name}")


def _handle_terminal_weather_text(body: ET.Element) -> tuple[int, str] | None:
    """Terminal Weather Text Normal/Special Product -- IMPLEMENTED
    2026-08-30 (SWIM audit blind sweep). Confirmed real structure via
    itws_debug_by_product/Terminal_Weather_Text_{Normal,Special}_Product.xml
    (both PCT-site, IAD/DCA -- DC-local, tiny payloads):
        twx_text_prod > twx_msg_type, twx_size, twx_text (free text, e.g.
            "KIAD 1249 ITWS TERMINAL WX -NO STORM WITHIN 15NM"     [Normal]
            "KDCA WSA CANC 21"                                     [Special]
    The module docstring's earlier "named-but-never-captured" note for the
    Normal product is now stale -- both products have real captures and
    the Special variant was never even in _KNOWN_UNHANDLED_PRODUCTS, so it
    was logging as unrecognized on every arrival. Same shape as
    _handle_terminal_weather_graphics: ITWS renders its own summary line;
    "NO STORM" prefix marks the quiet state, anything else is surfaced
    verbatim at severity 3 (recorded/queryable, below the push threshold
    of 4 -- the ATIS/microburst/gust-front products remain the push-worthy
    hazard channel; this is corroborating context, not a new alarm)."""
    twx = _find30(body, "twx_text_prod")
    if twx is None:
        return None
    text = " ".join((_child_text(twx, "twx_text") or "").split())
    if not text:
        return None
    if "NO STORM" in text.upper():
        return (0, f"Terminal weather text: {text}")
    return (3, f"Terminal weather text: {text}")


def _handle_tornado_alert(body: ET.Element) -> tuple[int, str] | None:
    """tornado_alert -- IMPLEMENTED 2026-07-21. Confirmed real structure
    via itws_debug_by_product/Tornado_Alert_Product.xml:
        tornado_alert > trnal_exists_flag (0/1), trnal_radius (nm),
                         trnal_message (raw NWS warning text, e.g. "TORNADO")
    Previously stashed as unhandled alongside the genuine raster/heavy
    products -- this one is neither: it's a tiny flag+radius+text payload,
    and a tornado warning is unambiguously the highest-value, most
    actionable severe-weather signal ITWS carries for ground-transport
    dispatch. Fires at severity 6 (top of the documented 1-6 scale) when
    trnal_exists_flag=1, distinct from every other ITWS product handled
    here which tops out at 4-5.
    """
    tor = _find30(body, "tornado_alert")
    if tor is None:
        return None
    exists = (_child_text(tor, "trnal_exists_flag") or "0") == "1"
    if not exists:
        return (0, "Tornado alert: none active")
    radius = _child_text(tor, "trnal_radius") or "?"
    message = _child_text(tor, "trnal_message") or "TORNADO"
    return (6, f"TORNADO ALERT: {message} within {radius}nm")


# Exact product_msg_name -> (body_container_tag, handler). body_container_tag
# is informational only (handlers find their own container); kept for
# readability of what's implemented vs stubbed.
_PRODUCT_HANDLERS: dict[str, callable] = {
    "Microburst ATIS Product": _handle_microburst_atis,
    "Wind Shear ATIS Product": _handle_wind_shear_atis,
    "Hazard Text TRACON Product": _handle_hazard_text,
    "Hazard Text Long Range Product": _handle_hazard_text,
    "SM SEP TRACON Product": _handle_sm_sep,
    "SM SEP Long Range Product": _handle_sm_sep,
    "SM SEP 5nm Product": _handle_sm_sep,
    "AP Status": _handle_ap_status,
    "Gust Front ETI Product": _handle_gust_front_eti,
    "Hazard Text 5nm Product": _handle_hazard_text,
    "Terminal Weather Graphics Product": _handle_terminal_weather_graphics,
    "Tornado Alert Product": _handle_tornado_alert,
    # 2026-08-30 SWIM-audit additions -- all three confirmed against real
    # captures in itws_debug_by_product/ (see each handler's docstring).
    # Runway Configuration and Terminal Weather Text Special were never in
    # _KNOWN_UNHANDLED_PRODUCTS either, so they were logging as
    # unrecognized and being dropped on every arrival.
    "Runway Configuration Product": _handle_runway_configuration,
    "Terminal Weather Text Normal Product": _handle_terminal_weather_text,
    "Terminal Weather Text Special Product": _handle_terminal_weather_text,
}

# Confirmed-live but intentionally not handled yet -- raster/heavy payloads
# (Precipitation TRACON/Long Range/5nm, AP Indicated Precipitation, Forecast
# Image/Contour/Accuracy, Gust Front TRACON Map) need real grid-decode work,
# not a quick tag mapping. Wind Profile Product (confirmed real 2026-07-21,
# 8-altitude-band wind soundings per site) is raw instrument data with no
# alert threshold -- same "not a quick tag mapping" bucket, not a hazard
# feed. Configured Alerts Product (confirmed real) is a per-runway LLWAS/
# ribbon-display grid, several nested repeating groups -- genuine future
# work, not a registration gap. Tornado Detections Product and Terminal
# Weather Text Normal Product remain named-but-never-captured.
# Tornado Alert Product and Hazard Text 5nm Product moved OUT of this set
# 2026-07-21 -- both turned out to be simple confirmed-real payloads, now
# in _PRODUCT_HANDLERS (see _handle_tornado_alert, _handle_hazard_text).
_KNOWN_UNHANDLED_PRODUCTS = frozenset({
    "Precipitation TRACON Product", "Precipitation Long Range Product",
    "Precipitation 5nm Product", "AP Indicated Precipitation Product",
    "Forecast Image Product", "Forecast Contour Product",
    "Forecast Accuracy Product", "Gust Front TRACON Map Product",
    "Wind Profile Product", "Tornado Detections Product",
    "Configured Alerts Product",
    # "Terminal Weather Text Normal Product" moved OUT of this set
    # 2026-08-30 -- real captures landed for both the Normal and Special
    # text variants (tiny DC-local free-text payloads, nothing like the
    # raster products this set exists for) and both now have a real
    # handler, alongside the newly-recognized Runway Configuration
    # Product. See _handle_terminal_weather_text /
    # _handle_runway_configuration.
})


def _txt(elem: ET.Element | None, *tags: str) -> str | None:
    cur = elem
    for tag in tags:
        if cur is None:
            return None
        found = cur.find(tag)
        if found is None:
            for uri in _ITWS_NS.values():
                found = cur.find(f"{{{uri}}}{tag}")
                if found is not None:
                    break
        cur = found
    return (cur.text or "").strip() or None if cur is not None else None


def _parse_time(ts: str | None) -> str | None:
    if not ts:
        return None
    ts = ts.strip()
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ts


def _sanitize_xml(xml_bytes: bytes) -> bytes:
    """Strip illegal XML 1.0 characters that ITWS occasionally emits, and
    escape stray unescaped '<' characters found in text content.

    Legal XML 1.0 char ranges (spec 2.2): #x9 | #xA | #xD | [#x20-#xD7FF] |
    [#xE000-#xFFFD] | [#x10000-#x10FFFF]. The character class below used to
    have literal Unicode characters pasted in where \\uXXXX escapes belonged,
    which silently excluded the entire #xE000-#xFFFD range (private-use +
    most CJK/symbol blocks) from the *allowed* set -- legitimate characters
    in that range were stripped as if illegal, while a stray literal '-' and
    U+FFFD were individually whitelisted instead of used as range bounds.
    Fixed with explicit escapes (regression, 2026-07-19).

    Second issue found 2026-07-20 via _capture_parse_failure_sample: some
    ITWS text fields (e.g. <ca_rib_rbdt_id>FR1Y<</ca_rib_rbdt_id>) carry a
    raw, un-escaped '<' inside element text -- a structural well-formedness
    violation, not an illegal-character-range one, so the fix above didn't
    (and couldn't) catch it. Any '<' not immediately followed by a valid
    tag-start character (letter, '/', '?', '!') is almost certainly stray
    data, not markup, so it gets escaped to &lt; before parsing. Confirmed
    against the real captured failure sample (failure_0.xml) -- parses
    clean after this fix.
    """
    import re
    text = xml_bytes.decode("utf-8", errors="replace")
    # Remove characters outside the legal XML 1.0 character set
    text = re.sub(
        r"[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]",
        "", text,
    )
    # Escape stray '<' that isn't the start of a real tag/PI/comment/decl.
    text = re.sub(r"<(?![a-zA-Z/?!])", "&lt;", text)
    return text.encode("utf-8")


def parse_itws_message(xml_bytes: bytes) -> list[dict]:
    """
    Parse an ITWS NMS XML message using the real confirmed schema
    (itws_msg > product_header > product_msg_name routing key).

    Returns list of alert dicts: {airport, product_type, severity, detail,
    valid_time, expires_time, raw_json}. Filtered to DCA/IAD/BWI only
    (ITWS is nationwide; see ITWS_AIRPORTS docstring above).

    Every product currently in _PRODUCT_HANDLERS returns a result even when
    quiet (severity 0, "clear"/"none" detail) -- this keeps itws_alerts
    populated with current conditions at all times, not just during active
    hazards, same pattern as SMES's continuous surface_tracks. check_itws_alerts
    below decides whether a given severity actually fires an ntfy push.
    """
    if not xml_bytes:
        return []
    _maybe_capture_debug_sample(xml_bytes)
    try:
        root = ET.fromstring(_sanitize_xml(xml_bytes))
    except ET.ParseError as e:
        log.warning("itws: XML parse error: %s", e)
        _capture_parse_failure_sample(xml_bytes, str(e))
        return []

    if root.tag != "itws_msg":
        return []

    product_header = _find30(root, "product_header")
    if product_header is None:
        return []

    msg_id_elem = _find30(product_header, "product_header_msg_id")
    product_name = _child_text(msg_id_elem, "product_msg_name") if msg_id_elem is not None else None
    if not product_name:
        return []

    airport = _norm_airport(_child_text(product_header, "product_header_airports"))
    if airport not in ITWS_AIRPORTS:
        return []

    handler = _PRODUCT_HANDLERS.get(product_name)
    if handler is None:
        if product_name not in _KNOWN_UNHANDLED_PRODUCTS:
            log.info("itws: unrecognized product_msg_name %r at %s", product_name, airport)
        _maybe_capture_product_sample(xml_bytes, product_name)
        return []

    result = handler(root)
    if result is None:
        return []
    severity, detail = result

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "airport": airport,
        "product_type": product_name,
        "severity": severity,
        "detail": detail,
        "valid_time": now,
        "expires_time": None,
        "source": "swim_itws",
    }
    return [{**payload, "raw_json": json.dumps(payload)}]


def _parse_itws_message_legacy_guess(xml_bytes: bytes) -> list[dict]:
    """Original tag-guessing implementation -- itwsProduct/weatherAlert/etc
    tags never matched anything real (root is actually itws_msg, confirmed
    2026-07-20). Always returned []. Kept for reference only, not called.
    """
    try:
        root = ET.fromstring(_sanitize_xml(xml_bytes))
    except ET.ParseError:
        return []

    alerts: list[dict] = []
    raw_xml = xml_bytes.decode("utf-8", errors="replace")

    _ALERT_TAGS = {
        "itwsProduct", "weatherAlert", "terminalWeather",
        "precipAlert", "windShearAlert", "microburstAlert", "lightningAlert",
        "itwsAlert", "itwsData",
    }

    for elem in root.iter():
        local = elem.tag.split("}")[-1]
        if local in _ALERT_TAGS:
            alert = _parse_single_alert(elem, raw_xml)
            if alert and alert["airport"] in ITWS_AIRPORTS:
                alerts.append(alert)

    return alerts


def _parse_single_alert(elem: ET.Element, raw_xml: str) -> dict | None:
    airport = (
        _txt(elem, "airport") or
        _txt(elem, "facility") or
        _txt(elem, "icao")
    )
    if not airport:
        return None

    # Normalise airport — add K prefix if needed
    airport = airport.upper()
    if len(airport) == 3:
        airport = "K" + airport

    raw_type = (
        _txt(elem, "productType") or
        _txt(elem, "alertType") or
        _txt(elem, "type") or
        elem.tag.split("}")[-1].upper()
    )
    product_type = _PRODUCT_CANONICAL.get(
        (raw_type or "").upper(), (raw_type or "UNKNOWN").upper()
    )

    sev_raw = _txt(elem, "severity") or _txt(elem, "level") or _txt(elem, "intensity")
    severity: int | None = None
    if sev_raw:
        try:
            severity = int(float(sev_raw))
        except (ValueError, TypeError):
            pass

    detail = (
        _txt(elem, "detail") or
        _txt(elem, "description") or
        _txt(elem, "alertText") or
        _txt(elem, "text")
    )

    valid_time = _parse_time(
        _txt(elem, "validTime") or _txt(elem, "startTime") or _txt(elem, "issueTime")
    ) or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    expires_time = _parse_time(
        _txt(elem, "expiresTime") or _txt(elem, "endTime") or _txt(elem, "expireTime")
    )

    payload = {
        "airport": airport,
        "product_type": product_type,
        "severity": severity,
        "detail": detail,
        "valid_time": valid_time,
        "expires_time": expires_time,
        "source": "swim_itws",
    }

    return {**payload, "raw_json": json.dumps(payload)}


def write_itws_alerts(alerts: list[dict]) -> int:
    """Upsert ITWS alerts into itws_alerts table. Returns count written."""
    written = 0
    for a in alerts:
        try:
            db.upsert_itws_alert(
                airport=a["airport"],
                product_type=a["product_type"],
                severity=a.get("severity"),
                detail=a.get("detail"),
                valid_time=a["valid_time"],
                expires_time=a.get("expires_time"),
                raw_json=a["raw_json"],
            )
            written += 1
        except Exception as e:
            log.error("itws: db write error for %s/%s: %s",
                      a.get("airport"), a.get("product_type"), e)
    return written


# 2026-07-28: ITWS re-broadcasts its current product state on essentially
# every SWIM message for as long as the underlying condition holds (same
# hazard-cell count, same storm position, etc.), not just on a state
# change -- confirmed live: the same "N active hazard cell(s)" detail was
# firing an identical ntfy push roughly every 2 minutes for a single
# ongoing condition. There was no dedup here at all, unlike every other
# push path in this codebase (landing pushes, TFR enrichment, etc.) which
# all use PushDedup. Suppression window is intentionally shorter than the
# 1h default used elsewhere -- aviation weather hazards can escalate on a
# timescale where an hour of silence on a still-active severe alert is too
# long, but 2 minutes is far too chatty. 20 minutes re-fires periodically
# on a persisting hazard without spamming every SWIM tick, and any real
# content change (new severity, new detail text) fires immediately
# regardless of the window via PushDedup's content-hash comparison.
_itws_dedup = PushDedup("itws-alerts", dedup_secs=1200)  # 20 min


def check_itws_alerts(alerts: list[dict]) -> None:
    """Fire ntfy for high-severity ITWS alerts (severity >= 4), deduped per
    (airport, product_type) slot so an unchanged, still-active condition
    doesn't re-fire on every SWIM message re-broadcasting the same state.

    2026-08-03: ADDED a second push through
    shared.sector_coalesce.fire_family_alert("itws", ...) -- "itws-alerts"
    (escalating-only aggregate) and "itws-<zone>" per-zone, giving ITWS the
    same escalation-threshold + per-topic throttle protection as
    tbfm/tfms/fdps. The existing direct "wx-alerts" push is left in place
    unchanged (not replaced) -- wx-alerts is shared with NWS/METAR-derived
    weather content, not ITWS-exclusive, so removing ITWS from it would be
    a visibility regression for anyone only watching that one topic. Flag
    to the operator if wx-alerts should drop ITWS content once the new
    itws-alerts/itws-<zone> topics are confirmed live-subscribed."""
    for a in alerts:
        sev = a.get("severity") or 0
        if sev < ITWS_ALERT_SEVERITY:
            continue
        airport = a["airport"]
        product_type = a["product_type"]
        detail = a.get("detail") or product_type
        dedup_key = f"{airport}:{product_type}"
        hash_key = content_hash(f"{sev}:{detail}")
        if not _itws_dedup.should_push(dedup_key, hash_key):
            log.debug("itws: suppressing duplicate alert %s (unchanged within window)", dedup_key)
            continue
        title = f"ITWS {product_type} — {airport} (sev {sev})"
        dispatch = f"{airport}: {product_type} severity {sev}"
        try:
            _fire_ntfy_dual("wx-alerts", title, detail, dispatch, priority=4)
            _itws_dedup.record(dedup_key, hash_key)
        except Exception as e:
            log.error("itws: ntfy error for %s/%s: %s", airport, product_type, e)
        try:
            from shared.sector_coalesce import fire_family_alert
            fire_family_alert("itws", "itws", airport, title, detail, dispatch, base_priority=4)
        except Exception as e:
            log.error("itws: family-alert fire failed for %s/%s: %s", airport, product_type, e)
