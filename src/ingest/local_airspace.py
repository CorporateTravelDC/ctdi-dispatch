"""
ingest.local_airspace — Local airspace awareness: UltraFeeder ADS-B + ACARS router.

Polls:
  1. UltraFeeder tar1090 /data/aircraft.json every 15s (HTTP)
     ULTRAFEEDER_URL env var — same source the pusher flight monitor uses.
  2. ACARS router TCP stream every 10s (persisted connection, JSON-lines)
     ACARS_ROUTER_HOST / ACARS_ROUTER_PORT env vars.

Writes to: local_aircraft, acars_messages, local_airspace_alerts tables.
Stamps heartbeats for ultrafeeder and acars feeds every 30s while reachable.

ntfy topic routing (canonical — do not change):
  flight-alerts + dispatch  — watched aircraft proximity alerts
  dispatch (only)           — Marine One / squawk 7700/7500/7600 local alerts

NOTE: Landing detection for watchlist sessions is owned by the pusher's
push_flight_watchlist_landings(). This module handles proximity/arrival
alerting only; it does not manage session lifecycle.
"""
from __future__ import annotations

import re
import json
import logging
import math
import os
import queue
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from common import acars_flags, db, tfr_geo
from common.push_dedup import PushDedup
from shared.watchlist import get_active_entries, watchlist_event_hit

log = logging.getLogger("ingest.local_airspace")

# ── Configuration ──────────────────────────────────────────────────────────────

_uf_base = os.environ.get("ULTRAFEEDER_URL", "").rstrip("/")
ULTRAFEEDER_AIRCRAFT_URL = f"{_uf_base}/data/aircraft.json" if _uf_base else ""

RECEIVER_LAT = float(os.environ.get("ULTRAFEEDER_LAT", "39.0000"))
RECEIVER_LON = float(os.environ.get("ULTRAFEEDER_LON", "-77.0000"))
SCAN_RADIUS_NM = float(os.environ.get("ULTRAFEEDER_SCAN_RADIUS_NM", "80"))
ALERT_RADIUS_NM = 30.0
MARINE_ONE_ALERT_RADIUS_NM = 50.0

ACARS_ROUTER_HOST = os.environ.get("ACARS_ROUTER_HOST", "host.containers.internal")
ACARS_ROUTER_PORT = int(os.environ.get("ACARS_ROUTER_PORT", "9080"))
# 2026-10-03: acars_router serves the plain-ACARS stream on 9080
# (AR_SERVE_TCP_ACARS) and the VDL2 stream on a separate port
# (AR_SERVE_TCP_VDLM2). This stack has no VHF ACARS decoder at all --
# every message is VDL2 from dumpvdl2 -- so reading only 9080 meant
# acars_messages never held a real row and the ACARS watchlist path was
# dead from the day it shipped (the 2026-08-22 idle warning had been
# firing every 30 min about exactly this; nobody was reading it). Both
# ports are read into the same queue; 0 disables the VDL2 reader.
VDLM2_ROUTER_PORT = int(os.environ.get("VDLM2_ROUTER_PORT", "15555"))

HEARTBEAT_DIR = Path(os.environ.get("DISPATCH_STATE_DIR",
                                     "/var/lib/corporatetraveldc")) / "feed_state"

UF_POLL_INTERVAL = 15      # seconds between UltraFeeder polls
ACARS_POLL_INTERVAL = 10   # seconds between ACARS queue drains
HEARTBEAT_INTERVAL = 30    # seconds between heartbeat stamps
ALERT_DEDUP_SECS = 300     # 5-minute dedup window for proximity alerts
# connected-but-zero-messages-ever warning threshold, throttled at the same
# interval -- see the 2026-08-22 acars_messages-zero-rows fix in
# LocalAirspaceMonitor.run_forever()
ACARS_IDLE_WARN_S = int(os.environ.get("ACARS_IDLE_WARN_S", "1800"))

# ── VIP / Emergency constants (aligned with FDPS parser) ──────────────────────

MARINE_ONE_CALLSIGNS = frozenset({
    "MARINE1", "MARINE2", "SAM", "AF1", "AF2", "EXEC1F",
    "VENUS", "MUSEL", "AZAZ01", "AZAZ09",
})
MARINE_ONE_SQUAWKS = frozenset({"7700", "5000", "5001"})
# 2026-10-05: CRANE + two digits (AF1/AF2 training aircraft), same rule as
# fdps_parser.VIP_CALLSIGN_PATTERNS.
_CRANE_CALLSIGN_RE = re.compile(r"^CRANE\d{2}$")
EMERGENCY_SQUAWKS = frozenset({"7700", "7500", "7600"})


def _vip_local_kind(cs_upper: str, squawk: str | None) -> str:
    """Label for a local VIP sighting, from what matched (2026-10-13)."""
    if cs_upper in ("MARINE1", "MARINE2"):
        return "Marine One" if cs_upper == "MARINE1" else "Marine Two"
    if _CRANE_CALLSIGN_RE.match(cs_upper):
        return "CRANE (AF1/AF2 training callsign)"
    if cs_upper in MARINE_ONE_CALLSIGNS:
        return f"VIP callsign {cs_upper}"
    if squawk in MARINE_ONE_SQUAWKS:
        return f"VIP squawk {squawk}"
    return "VIP"

# ── ACARS OOOI label table ─────────────────────────────────────────────────────

_ACARS_LABEL_OOOI: dict[str, str] = {
    "QM": "OUT",
    "QN": "OFF",
    "QO": "ON",
    "QP": "IN",
    "H1": "POS",    # position/ATIS — not strictly OOOI but useful
    "16": "DLY",    # delay report
    "B6": "OPOS",   # oceanic position
}

# ── ACARS type flags + TFR context (2026-10-18) ───────────────────────────────
# Every acars_messages row gets acars_flags (common.acars_flags.classify). The TFR
# ties use the areas the poller stores in tfrs.raw_json["areas_geo"] -- present
# only for VIP and SECURITY TFRs (the only types whose FAA detail record is
# fetched); other TFR types (hazards, sports, space ops) have no stored geometry,
# so for them only the text rule ("TFR"/"NOTAM"/"RESTRICTED AREA" ...) can flag.
#   tfr_notam_mention  text mentions a TFR/NOTAM/restricted area, or DIVERT/HOLD
#                      with a position within TFR_NEAR_NM of an active TFR area
#   tfr_area           a WATCHED flight's position report inside an active TFR
#                      area (lateral only; floors/ceilings are not checked)
# Flags are labels for filtering and event detail. They never set an OOOI phase:
# phases go through db.update_watchlist_oooi_phase_authoritative() on the gated
# paths only (tests/ingest/test_acars_flags_ingest.py holds that line).
TFR_NEAR_NM = 30.0
_TFR_AREAS_TTL_S = 60.0
_tfr_areas_cache: dict = {"at": -1e18, "areas": []}


def _active_tfr_areas() -> list[dict]:
    """Areas of the TFRs in effect now (start passed or unknown, not expired),
    read through a 60 s in-process cache -- one tfrs read a minute, not one per
    ACARS message. A read failure yields [] (no TFR flag), never an exception."""
    now = time.time()
    if now - _tfr_areas_cache["at"] < _TFR_AREAS_TTL_S:
        return _tfr_areas_cache["areas"]
    areas: list[dict] = []
    try:
        for t in db.get_active_tfrs():
            start = t.get("effective_start")
            if start and float(start) > now:
                continue
            try:
                d = json.loads(t.get("raw_json") or "{}") or {}
            except (TypeError, ValueError):
                continue
            # Each area carries its TFR's id and window, for the tfr_area push.
            areas.extend({**a, "tfr_id": t.get("tfr_id"), "tfr_start": t.get("effective_start"),
                          "tfr_end": t.get("effective_end")}
                         for a in (d.get("areas_geo") or []) if isinstance(a, dict))
    except Exception as e:
        log.debug("acars: active TFR areas unavailable: %s", e)
    _tfr_areas_cache.update(at=now, areas=areas)
    return areas


def _tfr_area_at(lat: float, lon: float) -> dict | None:
    """The first active TFR area containing (lat, lon), with its tfr_id/window."""
    for a in _active_tfr_areas():
        if tfr_geo.point_in_area(lat, lon, a):
            return a
    return None


# ── Flag pushes for watched flights (2026-10-18, operator) ─────────────────────
# A WATCHED flight whose message is flagged tfr_area or diversion pushes through
# watchlist_event_hit (flight-alerts + dispatch) at p4, or p5 when the entry is
# VIP-listed (shared.vip_listed). Dedup on content only, never time:
#   tfr_area   (watch id, TFR notam id)
#   diversion  (watch id, new destination; "unstated" when the text names none)
# The push names the callsign and the TFR id + its FAA window (UTC + ET), or the
# new destination -- never a position, a registration or a hex. OOOI is untouched:
# these are events, not phases.
_FLAG_PUSH_DEDUP = PushDedup("acars_flag_push")
_DIVERT_DEST_RE = re.compile(r"\b(?:DIVERT(?:ING|ED)?|DVRT)\s+(?:TO\s+)?([A-Z]{3,4})\b")
_NOT_AIRPORTS = frozenset({"DUE", "FOR", "NOW", "ASAP", "THE", "AND", "BACK", "WILL", "WX",
                           "MED", "OUR", "YOUR", "PLAN", "REQ", "INTO", "FROM", "ALTN"})


def _divert_destination(text: str | None) -> str:
    m = _DIVERT_DEST_RE.search((text or "").upper())
    if m and m.group(1) not in _NOT_AIRPORTS:
        return m.group(1)
    return "unstated"


def _utc_et(epoch) -> str:
    if not epoch:
        return "?"
    from zoneinfo import ZoneInfo
    u = datetime.fromtimestamp(float(epoch), timezone.utc)
    et = u.astimezone(ZoneInfo("America/New_York"))
    return f"{u:%H:%M}Z / {et:%H:%M} ET {et:%a %m-%d}"


def _flag_pushes(entry: dict, ident: str, flight: str | None, msg: dict, flags: list[str]) -> int:
    """Fire the tfr_area / diversion pushes for one watched-flight message. Returns
    the number fired. Never raises into the ingest path."""
    from shared.vip_listed import is_vip_listed
    if "tfr_area" not in flags and "diversion" not in flags:
        return 0
    priority = 5 if is_vip_listed(entry) else 4
    callsign = flight or ident
    fired = 0
    if "tfr_area" in flags:
        f = acars_flags.extract_fields(msg)
        area = _tfr_area_at(f["lat"], f["lon"]) if f["lat"] is not None else None
        tid = (area or {}).get("tfr_id")
        if tid:
            key = f"{entry['id']}:tfr_area:{tid}"
            if _FLAG_PUSH_DEDUP.should_push(key, "1"):
                window = f"Active {_utc_et(area.get('tfr_start'))} to {_utc_et(area.get('tfr_end'))}"
                watchlist_event_hit(
                    entry["id"], f"{callsign} inside active TFR {tid}\n{window}",
                    {"watchlist_trigger": "acars_tfr_area", "identifier": ident,
                     "tfr_id": tid, "source": "acars", "acars_flag": "tfr_area"},
                    priority=priority)
                _FLAG_PUSH_DEDUP.record(key, "1")
                fired += 1
    if "diversion" in flags:
        dest = _divert_destination(acars_flags.extract_fields(msg)["text"])
        key = f"{entry['id']}:diversion:{dest}"
        if _FLAG_PUSH_DEDUP.should_push(key, "1"):
            where = f"diverting to {dest}" if dest != "unstated" else "diverting (destination not stated)"
            watchlist_event_hit(
                entry["id"], f"{callsign} ACARS: {where}",
                {"watchlist_trigger": "acars_diversion", "identifier": ident,
                 "new_destination": dest, "source": "acars", "acars_flag": "diversion"},
                priority=priority)
            _FLAG_PUSH_DEDUP.record(key, "1")
            fired += 1
    return fired


def _acars_message_flags(msg: dict, watched: bool) -> list[str]:
    areas = _active_tfr_areas()
    near = (lambda la, lo: tfr_geo.point_in_any(la, lo, areas, TFR_NEAR_NM)) if areas else None
    inside = (lambda la, lo: tfr_geo.point_in_any(la, lo, areas)) if (areas and watched) else None
    return acars_flags.classify(msg, in_tfr=inside, near_tfr=near)


# ── Haversine distance ─────────────────────────────────────────────────────────

def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3440.065  # Earth radius in nautical miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Heartbeat stamping ────────────────────────────────────────────────────────

def _stamp_heartbeat(feed_name: str) -> None:
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    hb_file = HEARTBEAT_DIR / f"{feed_name}.heartbeat"
    hb_file.write_text(str(time.time()))


# ── ntfy helper (local alerts not routed through watchlist_event_hit) ─────────

def _fire_ntfy(topic: str, title: str, body: str, priority: int) -> None:
    from shared.watchlist import NTFY_BASE, NTFY_TOKEN, NTFY_USER, NTFY_PASS
    safe_title = title.encode("ascii", "replace").decode("ascii")
    headers = {
        "Content-Type": "text/plain",
        "X-Priority": str(priority),
        "X-Title": safe_title,
    }
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN.split(':')[0]}"
    auth = None
    if NTFY_USER and not NTFY_TOKEN:
        auth = (NTFY_USER, NTFY_PASS)
    try:
        requests.post(f"{NTFY_BASE}/{topic}", data=body.encode("utf-8"),
                      headers=headers, auth=auth, timeout=10)
    except Exception as e:
        log.error("ntfy %s FAILED: %s", topic, e)


# ── Deduplication (local, separate from watchlist dedup) ─────────────────────

_local_dedup_lock = threading.Lock()
_local_dedup: dict[str, float] = {}  # key → last fired epoch


def _local_dedup_check(key: str, window_secs: int = ALERT_DEDUP_SECS) -> bool:
    """Return True if suppressed (fired within window)."""
    now = time.time()
    with _local_dedup_lock:
        if now - _local_dedup.get(key, 0.0) < window_secs:
            return True
        _local_dedup[key] = now
        return False


# ── UltraFeeder poll ──────────────────────────────────────────────────────────

def _poll_ultrafeeder() -> bool:
    """
    Fetch aircraft.json from UltraFeeder. Returns True if reachable.
    For each aircraft: upsert local_aircraft, check watchlist, check emergencies.
    """
    if not ULTRAFEEDER_AIRCRAFT_URL:
        return False

    try:
        resp = requests.get(ULTRAFEEDER_AIRCRAFT_URL, timeout=8)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("UltraFeeder unreachable: %s", e)
        return False

    aircraft_list = data.get("aircraft", [])
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_ts = time.time()

    # Load active watchlist entries once per poll cycle.
    try:
        wl_entries = get_active_entries(entry_type="flight")
    except Exception:
        wl_entries = []

    for ac in aircraft_list:
        icao_hex = (ac.get("hex") or "").lower().strip()
        if not icao_hex:
            continue

        callsign = (ac.get("flight") or "").strip() or None
        registration = ac.get("r") or None
        aircraft_type = ac.get("t") or None
        lat = ac.get("lat")
        lon = ac.get("lon")
        alt_baro = ac.get("alt_baro")
        alt_geom = ac.get("alt_geom")
        gs = ac.get("gs")
        track = ac.get("track")
        squawk = ac.get("squawk") or None
        rssi = ac.get("rssi")
        seen_ago = ac.get("seen", 0) or 0

        # Altitude
        if alt_baro == "ground":
            altitude_ft = 0
            on_ground = 1
        elif isinstance(alt_baro, (int, float)):
            altitude_ft = int(alt_baro)
            on_ground = 1 if altitude_ft < 100 else 0
        elif isinstance(alt_geom, (int, float)):
            altitude_ft = int(alt_geom)
            on_ground = 0
        else:
            altitude_ft = None
            on_ground = 0

        # Speed — also used to refine on_ground
        ground_speed = int(gs) if isinstance(gs, (int, float)) else None
        if ground_speed is not None and ground_speed < 80 and (altitude_ft or 0) < 100:
            on_ground = 1

        # Last seen timestamp
        last_seen_ts = now_ts - seen_ago
        last_seen = datetime.fromtimestamp(last_seen_ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")

        # Distance from receiver
        distance_nm: Optional[float] = None
        if lat is not None and lon is not None:
            distance_nm = _haversine_nm(RECEIVER_LAT, RECEIVER_LON, lat, lon)

        # Upsert into local_aircraft
        try:
            db.upsert_local_aircraft(
                icao_hex=icao_hex,
                callsign=callsign,
                registration=registration,
                aircraft_type=aircraft_type,
                latitude=lat,
                longitude=lon,
                altitude_ft=altitude_ft,
                ground_speed=ground_speed,
                track_deg=float(track) if track is not None else None,
                squawk=squawk,
                on_ground=on_ground,
                rssi=float(rssi) if rssi is not None else None,
                distance_nm=distance_nm,
                last_seen=last_seen,
            )
        except Exception as e:
            log.debug("local_aircraft upsert %s: %s", icao_hex, e)
            continue

        # Skip alert logic if no position
        if lat is None or lon is None or distance_nm is None:
            continue

        cs_upper = (callsign or "").upper()

        # Marine One detection
        is_marine_one = (cs_upper in MARINE_ONE_CALLSIGNS or
                         bool(_CRANE_CALLSIGN_RE.match(cs_upper)) or
                         squawk in MARINE_ONE_SQUAWKS)
        if is_marine_one and distance_nm <= MARINE_ONE_ALERT_RADIUS_NM:
            # 2026-10-13 (operator): CRANE is a flight identifier (the AF1/AF2 training
            # callsign family), not Marine One -- name what actually matched.
            vip_kind = _vip_local_kind(cs_upper, squawk)
            dedup_key = f"marine_one:{icao_hex}"
            if not _local_dedup_check(dedup_key, ALERT_DEDUP_SECS):
                alt_str = f"{altitude_ft:,}ft" if altitude_ft is not None else "alt unknown"
                body = (f"{cs_upper or icao_hex} {distance_nm:.1f}nm | {alt_str}\n"
                        f"ICAO: {icao_hex} | squawk: {squawk or '—'}")
                _fire_ntfy("dispatch", f"{vip_kind} local: {cs_upper or icao_hex}",
                           body, priority=5)
                try:
                    db.insert_local_airspace_alert(
                        fired_at=now_iso, alert_type="marine_one_local",
                        icao_hex=icao_hex, callsign=callsign,
                        registration=registration,
                        distance_nm=distance_nm, altitude_ft=altitude_ft,
                        squawk=squawk, watchlist_entry_id=None,
                        payload={"icao_hex": icao_hex, "callsign": cs_upper,
                                 "distance_nm": distance_nm,
                                 "altitude_ft": altitude_ft, "squawk": squawk},
                        ntfy_fired=1,
                    )
                    # Also write to hot_alerts for the pusher's awareness
                    db.insert_route_narrative(
                        f"{vip_kind} LOCAL: {cs_upper or icao_hex} "
                        f"{distance_nm:.1f}nm | {alt_str}",
                        [], [cs_upper or icao_hex],
                    )
                except Exception as e:
                    log.debug("marine_one_local DB write: %s", e)
                log.info("%s LOCAL: %s %.1fnm alt=%s", vip_kind, cs_upper, distance_nm, altitude_ft)

        # Emergency squawk detection (within scan radius)
        if squawk in EMERGENCY_SQUAWKS and distance_nm <= SCAN_RADIUS_NM:
            dedup_key = f"squawk:{icao_hex}:{squawk}"
            if not _local_dedup_check(dedup_key, ALERT_DEDUP_SECS):
                squawk_names = {"7700": "EMERGENCY", "7500": "HIJACK", "7600": "COMMS LOSS"}
                label = squawk_names.get(squawk, squawk)
                alt_str = f"{altitude_ft:,}ft" if altitude_ft is not None else "alt unknown"
                body = (f"{cs_upper or icao_hex} squawk {squawk} ({label})\n"
                        f"{distance_nm:.1f}nm | {alt_str} | ICAO: {icao_hex}")
                _fire_ntfy("dispatch", f"Squawk {squawk}: {cs_upper or icao_hex}",
                           body, priority=4)
                try:
                    db.insert_local_airspace_alert(
                        fired_at=now_iso, alert_type=f"squawk_{squawk}",
                        icao_hex=icao_hex, callsign=callsign,
                        registration=registration,
                        distance_nm=distance_nm, altitude_ft=altitude_ft,
                        squawk=squawk, watchlist_entry_id=None,
                        payload={"icao_hex": icao_hex, "callsign": cs_upper,
                                 "squawk": squawk, "label": label,
                                 "distance_nm": distance_nm,
                                 "altitude_ft": altitude_ft},
                        ntfy_fired=1,
                    )
                except Exception as e:
                    log.debug("squawk alert DB write: %s", e)
                log.info("Emergency squawk %s: %s %.1fnm", squawk, cs_upper or icao_hex, distance_nm)

        # Watchlist proximity check
        if distance_nm <= ALERT_RADIUS_NM:
            for entry in wl_entries:
                ident = entry["identifier"].upper()
                match = (cs_upper and cs_upper == ident) or \
                        (registration and registration.upper() == ident)
                if not match:
                    continue
                dedup_key = f"proximity:{entry['id']}:{icao_hex}"
                if _local_dedup_check(dedup_key, ALERT_DEDUP_SECS):
                    continue
                alt_str = f"{altitude_ft:,}ft" if altitude_ft is not None else "alt unknown"
                tracking = f"https://globe.airplanes.live/?icao={icao_hex}"
                summary = (f"{ident} in local range: {distance_nm:.1f}nm | "
                           f"{alt_str} | ICAO: {icao_hex}")
                try:
                    watchlist_event_hit(
                        entry["id"],
                        summary,
                        {"watchlist_trigger": "watchlist_proximity",
                         "identifier": ident, "icao_hex": icao_hex,
                         "distance_nm": distance_nm, "altitude_ft": altitude_ft,
                         "tracking_url": tracking, "source": "ultrafeeder"},
                        priority=4,
                    )
                    db.insert_local_airspace_alert(
                        fired_at=now_iso, alert_type="watchlist_proximity",
                        icao_hex=icao_hex, callsign=callsign,
                        registration=registration,
                        distance_nm=distance_nm, altitude_ft=altitude_ft,
                        squawk=squawk, watchlist_entry_id=entry["id"],
                        payload={"identifier": ident, "tracking_url": tracking},
                        ntfy_fired=1,
                    )
                except Exception as e:
                    log.debug("watchlist proximity alert %s: %s", ident, e)
                log.info("Watchlist proximity: %s %.1fnm", ident, distance_nm)

    return True


# ── ACARS TCP reader ──────────────────────────────────────────────────────────

class _AcarsReader(threading.Thread):
    """
    Persistent TCP connection to acarsrouter. Reads newline-delimited JSON.
    Messages are pushed to an internal queue for the main poll loop to drain.
    Reconnects automatically on disconnect.
    """

    def __init__(self, host: str, port: int, msg_queue: queue.Queue) -> None:
        super().__init__(daemon=True, name="acars-tcp-reader")
        self._host = host
        self._port = port
        self._queue = msg_queue
        self._stop = threading.Event()
        self._connected = threading.Event()
        # 2026-08-22: acars_messages had zero rows ever despite a
        # continuously-fresh heartbeat -- the heartbeat only ever checked
        # is_connected() (socket liveness), never whether any line had
        # actually been received or parsed successfully. Third instance
        # of this exact "heartbeat proves the socket, not the data" trap
        # found in this codebase the same night (see the NWWS/nws_alerts
        # and NWWS/push:nws root-causes). These counters make real
        # traffic volume and parse-failure rate visible via ordinary logs
        # instead of needing another temporary-diagnostic-then-remove
        # cycle to find out what's actually happening upstream.
        self.lines_received = 0
        self.parse_failures = 0
        self.last_parse_failure_sample: bytes | None = None

    def stop(self) -> None:
        self._stop.set()

    def is_connected(self) -> bool:
        return self._connected.is_set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                with socket.create_connection((self._host, self._port), timeout=10) as sock:
                    sock.settimeout(30)
                    self._connected.set()
                    log.info("ACARS router connected %s:%d", self._host, self._port)
                    buf = b""
                    while not self._stop.is_set():
                        try:
                            chunk = sock.recv(4096)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            self.lines_received += 1
                            try:
                                msg = json.loads(line)
                                self._queue.put_nowait(msg)
                            except (json.JSONDecodeError, queue.Full) as e:
                                self.parse_failures += 1
                                self.last_parse_failure_sample = line[:200]
                                if self.parse_failures <= 5 or self.parse_failures % 100 == 0:
                                    log.warning(
                                        "ACARS line dropped (%s) failures=%d received=%d sample=%r",
                                        type(e).__name__, self.parse_failures,
                                        self.lines_received, self.last_parse_failure_sample,
                                    )
            except Exception as e:
                log.warning("ACARS router connection error: %s — retrying in 15s", e)
            finally:
                self._connected.clear()
            if not self._stop.is_set():
                self._stop.wait(15)


def _normalize_acars(msg: dict) -> Optional[dict]:
    """Flatten a dumpvdl2 VDL2 frame into the acarsdec-style dict the rest
    of this module expects. acarsdec/plain-ACARS lines pass through
    unchanged. Returns None for VDL2 frames that carry no ACARS payload
    (AVLC supervisory/XID frames -- the majority of what dumpvdl2 emits).

    dumpvdl2 shape (captured live 2026-10-03 from acars_router :15555):
      {"vdl2": {"freq": 136975000, "station": "CS-KDCA-VDL",
                "t": {"sec": ..., "usec": ...},
                "avlc": {"src": {"addr": "DC5068", "type": "Aircraft", ...},
                         "acars": {"reg": ".Q0LYY", "flight": "WN4060",
                                   "label": "H1", "blk_id": "2", "ack": "!",
                                   "mode": "2", "msg_text": "..."}}}}
    reg carries a leading "." pad; freq is Hz, not MHz.
    """
    if not isinstance(msg, dict):
        return None
    if "vdl2" not in msg:
        return msg                      # acarsdec / plain-ACARS line
    v = msg.get("vdl2")
    if not isinstance(v, dict):
        return None
    avlc = v.get("avlc") or {}
    ac = avlc.get("acars")
    if not isinstance(ac, dict):
        return None
    src = avlc.get("src") or {}
    reg = (ac.get("reg") or "").strip().lstrip(".") or None
    freq = v.get("freq")
    try:
        freq_mhz = float(freq) / 1e6 if freq is not None else None
    except (TypeError, ValueError):
        freq_mhz = None
    out = {
        "tail": reg,
        "flight": (ac.get("flight") or "").strip() or None,
        "label": ac.get("label"),
        "sublabel": ac.get("sublabel"),
        # 2026-10-18: AVLC source type decides the link direction for the type flags.
        "direction": ("downlink" if src.get("type") == "Aircraft"
                      else "uplink" if src.get("type") in ("Ground station", "Ground") else ""),
        "freq": freq_mhz,
        "icao": (src.get("addr") or "") if src.get("type") in (None, "Aircraft") else "",
        "text": ac.get("msg_text"),
        "block_id": ac.get("blk_id"),
        "ack": ac.get("ack"),
        "mode": ac.get("mode"),
        "station": v.get("station"),
        "source": "vdlm2",
        "_raw": msg,  # original frame kept for the raw column
    }
    return out


def _process_acars_message(msg: dict) -> None:
    """Parse one ACARS message dict and write to DB; fire watchlist alert if matched."""
    msg = _normalize_acars(msg)
    if msg is None:
        return
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tail = (msg.get("tail") or msg.get("tail_number") or
            msg.get("registration") or "").strip() or None
    flight = (msg.get("flight") or msg.get("callsign") or "").strip() or None
    label = (msg.get("label") or "").strip() or None
    freq = msg.get("freq") or msg.get("frequency")
    icao_hex = (msg.get("icao") or msg.get("icao_hex") or "").lower() or None
    msg_text = msg.get("text") or msg.get("msg_text") or msg.get("data") or None
    block_id = str(msg.get("block_id", "") or "").strip() or None
    ack = str(msg.get("ack", "") or "").strip() or None
    mode = str(msg.get("mode", "") or "").strip() or None
    raw = json.dumps(msg.get("_raw", msg))

    freq_mhz: Optional[float] = None
    if freq is not None:
        try:
            freq_mhz = float(freq)
        except (ValueError, TypeError):
            pass

    msg_type = _ACARS_LABEL_OOOI.get(label or "", None)

    # Watchlist match
    watchlist_hit = 0
    watchlist_entry_id = None
    matched = None
    try:
        # 2026-10-03: ACARS/VDL2 `flight` is IATA-style (WN4060) while
        # watchlist identifiers are ICAO (SWA4060); exact-match never fired
        # on a real message. Compare both sides in ICAO form (unknown
        # carriers normalise to None and cannot false-match), and also match
        # the ACARS tail against the entry's registration column, not only
        # its identifier.
        # 2026-10-18: cached read (3 s, the FDPS/SMES per-message pattern).
        from common.airline_codes import same_flight
        entries = get_active_entries(entry_type="flight", cached=True)
        tail_upper = (tail or "").upper().replace("-", "")
        for entry in entries:
            ident = (entry.get("identifier") or "").upper()
            reg = (entry.get("registration") or "").upper().replace("-", "")
            if (flight and same_flight(flight, ident)) or \
               (tail_upper and tail_upper in (ident.replace("-", ""), reg)):
                watchlist_hit = 1
                watchlist_entry_id = entry["id"]
                matched = (entry, ident)
                break
    except Exception as e:
        log.debug("acars watchlist match: %s", e)

    # 2026-10-18: type flags (classified from the same normalized fields).
    try:
        flags = _acars_message_flags(msg, watched=bool(watchlist_hit))
    except Exception as e:
        log.debug("acars flags: %s", e)
        flags = []

    if matched and flags:
        try:
            _flag_pushes(matched[0], matched[1], flight, msg, flags)
        except Exception as e:
            log.debug("acars flag push: %s", e)

    if matched and msg_type:
        entry, ident = matched
        try:
            summary = (f"{ident} ACARS {msg_type}"
                       + (f": {msg_text[:80]}" if msg_text else ""))
            watchlist_event_hit(
                entry["id"], summary,
                {"watchlist_trigger": f"acars_{msg_type.lower()}",
                 "identifier": ident, "label": label,
                 "msg_type": msg_type, "tail": tail,
                 "icao_hex": icao_hex, "msg_text": msg_text,
                 "source": "acars",
                 # 2026-10-18: subscribers filter on these (event detail, same
                 # topic); a flag is a label, never a phase assertion.
                 "acars_flags": flags},
                priority=4 if msg_type in ("OFF", "ON", "IN") else 3,
            )
        except Exception as e:
            log.debug("acars watchlist event: %s", e)

    try:
        db.insert_acars_message(
            received_at=now_iso, freq_mhz=freq_mhz, icao_hex=icao_hex,
            tail=tail, flight=flight, msg_type=msg_type, label=label,
            block_id=block_id, ack=ack, mode=mode, msg_text=msg_text,
            raw=raw, watchlist_hit=watchlist_hit,
            watchlist_entry_id=watchlist_entry_id,
            acars_flags=acars_flags.to_column(flags),
        )
    except Exception as e:
        log.debug("acars_messages insert: %s", e)


# ── Main monitor class ────────────────────────────────────────────────────────

class LocalAirspaceMonitor:
    """
    Run in a background thread from ingest.main.
    Polls UltraFeeder ADS-B and ACARS router independently.
    Never crashes the container — catches all exceptions per feed.
    """

    def __init__(self) -> None:
        self._acars_queue: queue.Queue = queue.Queue(maxsize=500)
        self._acars_reader: Optional[_AcarsReader] = None   # plain ACARS (:9080)
        self._vdlm2_reader: Optional[_AcarsReader] = None   # VDL2 (:15555)
        self._last_uf_poll = 0.0
        self._last_acars_drain = 0.0
        self._last_uf_hb = 0.0
        self._last_acars_hb = 0.0
        # 2026-08-22 acars_messages-zero-rows fix: track the last time we
        # actually drained a message (not just had a live socket) so we
        # can warn when the connection is up but nothing has come through
        # for a long stretch -- the exact condition that ran silently for
        # weeks before this fix because the heartbeat only checked
        # is_connected().
        self._acars_started_at = 0.0
        self._last_acars_data_at = 0.0
        self._last_acars_idle_warn = 0.0

    def _start_acars_reader(self) -> None:
        started = False
        if self._acars_reader is None or not self._acars_reader.is_alive():
            self._acars_reader = _AcarsReader(
                ACARS_ROUTER_HOST, ACARS_ROUTER_PORT, self._acars_queue)
            self._acars_reader.start()
            started = True
        if VDLM2_ROUTER_PORT and (self._vdlm2_reader is None or
                                  not self._vdlm2_reader.is_alive()):
            self._vdlm2_reader = _AcarsReader(
                ACARS_ROUTER_HOST, VDLM2_ROUTER_PORT, self._acars_queue)
            self._vdlm2_reader.start()
            started = True
        if started:
            self._acars_started_at = time.time()
            self._last_acars_data_at = 0.0

    def _readers(self) -> list:
        return [r for r in (self._acars_reader, self._vdlm2_reader) if r is not None]

    def run_forever(self) -> None:
        """Main loop. Runs until the process exits."""
        self._start_acars_reader()
        log.info("LocalAirspaceMonitor started (UF=%s, ACARS=%s:%d, VDLM2=%s)",
                 ULTRAFEEDER_AIRCRAFT_URL or "disabled",
                 ACARS_ROUTER_HOST, ACARS_ROUTER_PORT,
                 f"{ACARS_ROUTER_HOST}:{VDLM2_ROUTER_PORT}" if VDLM2_ROUTER_PORT else "disabled")

        while True:
            now = time.time()

            # UltraFeeder poll
            if now - self._last_uf_poll >= UF_POLL_INTERVAL:
                self._last_uf_poll = now
                try:
                    reachable = _poll_ultrafeeder()
                    if reachable and now - self._last_uf_hb >= HEARTBEAT_INTERVAL:
                        _stamp_heartbeat("ultrafeeder")
                        self._last_uf_hb = now
                    elif not reachable:
                        log.debug("UltraFeeder not reachable — heartbeat not stamped")
                except Exception as e:
                    log.error("UltraFeeder poll error: %s", e)

            # ACARS queue drain
            if now - self._last_acars_drain >= ACARS_POLL_INTERVAL:
                self._last_acars_drain = now
                try:
                    drained = 0
                    while True:
                        try:
                            msg = self._acars_queue.get_nowait()
                            _process_acars_message(msg)
                            drained += 1
                        except queue.Empty:
                            break
                    if drained:
                        self._last_acars_data_at = now
                    # "up" = at least one router stream connected; the
                    # idle warning below counts lines across both.
                    acars_up = any(r.is_connected() for r in self._readers())
                    # 2026-08-22: heartbeat previously reflected only
                    # is_connected() -- a live TCP socket with zero
                    # messages ever parsed still stamped a fresh
                    # heartbeat every cycle, which is exactly how
                    # acars_messages sat at zero rows for weeks without
                    # tripping any alert. Heartbeat still gates on
                    # connection only (that's genuinely what it means:
                    # "is the reader thread alive"), but idle-with-
                    # connection now gets its own loud, throttled signal
                    # instead of staying silent.
                    if acars_up and now - self._last_acars_hb >= HEARTBEAT_INTERVAL:
                        _stamp_heartbeat("acars")
                        self._last_acars_hb = now
                    elif not acars_up:
                        log.debug("ACARS reader not connected — heartbeat not stamped")
                        self._start_acars_reader()
                    if drained:
                        log.debug("ACARS: drained %d message(s)", drained)
                    elif (acars_up and self._last_acars_data_at == 0.0 and
                          self._acars_started_at and
                          now - self._acars_started_at >= ACARS_IDLE_WARN_S and
                          now - self._last_acars_idle_warn >= ACARS_IDLE_WARN_S):
                        readers = self._readers()
                        log.warning(
                            "ACARS connected but zero messages parsed in %ds "
                            "(lines_received=%d parse_failures=%d) — router may "
                            "be silent or emitting a format this reader can't "
                            "parse; connection alone is not proof of data flow",
                            int(now - self._acars_started_at),
                            sum(r.lines_received for r in readers) if readers else -1,
                            sum(r.parse_failures for r in readers) if readers else -1,
                        )
                        self._last_acars_idle_warn = now
                except Exception as e:
                    log.error("ACARS drain error: %s", e)

            time.sleep(2)
