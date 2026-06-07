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

from common import db
from shared.watchlist import get_active_entries, watchlist_event_hit

log = logging.getLogger("ingest.local_airspace")

# ── Configuration ──────────────────────────────────────────────────────────────

_uf_base = os.environ.get("ULTRAFEEDER_URL", "").rstrip("/")
ULTRAFEEDER_AIRCRAFT_URL = f"{_uf_base}/data/aircraft.json" if _uf_base else ""

RECEIVER_LAT = float(os.environ.get("ULTRAFEEDER_LAT", "38.8816"))
RECEIVER_LON = float(os.environ.get("ULTRAFEEDER_LON", "-77.0910"))
SCAN_RADIUS_NM = float(os.environ.get("ULTRAFEEDER_SCAN_RADIUS_NM", "80"))
ALERT_RADIUS_NM = 30.0
MARINE_ONE_ALERT_RADIUS_NM = 50.0

ACARS_ROUTER_HOST = os.environ.get("ACARS_ROUTER_HOST", "host.containers.internal")
ACARS_ROUTER_PORT = int(os.environ.get("ACARS_ROUTER_PORT", "9080"))

HEARTBEAT_DIR = Path(os.environ.get("DISPATCH_STATE_DIR",
                                     "/var/lib/corporatetraveldc")) / "feed_state"

UF_POLL_INTERVAL = 15      # seconds between UltraFeeder polls
ACARS_POLL_INTERVAL = 10   # seconds between ACARS queue drains
HEARTBEAT_INTERVAL = 30    # seconds between heartbeat stamps
ALERT_DEDUP_SECS = 300     # 5-minute dedup window for proximity alerts

# ── VIP / Emergency constants (aligned with FDPS parser) ──────────────────────

MARINE_ONE_CALLSIGNS = frozenset({
    "MARINE1", "MARINE2", "SAM", "AF1", "AF2", "EXEC1F",
    "VENUS", "MUSEL", "AZAZ01", "AZAZ09",
})
MARINE_ONE_SQUAWKS = frozenset({"7700", "5000", "5001"})
EMERGENCY_SQUAWKS = frozenset({"7700", "7500", "7600"})

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
                         squawk in MARINE_ONE_SQUAWKS)
        if is_marine_one and distance_nm <= MARINE_ONE_ALERT_RADIUS_NM:
            dedup_key = f"marine_one:{icao_hex}"
            if not _local_dedup_check(dedup_key, ALERT_DEDUP_SECS):
                alt_str = f"{altitude_ft:,}ft" if altitude_ft is not None else "alt unknown"
                body = (f"{cs_upper or icao_hex} {distance_nm:.1f}nm | {alt_str}\n"
                        f"ICAO: {icao_hex} | squawk: {squawk or '—'}")
                _fire_ntfy("dispatch", f"Marine One local: {cs_upper or icao_hex}",
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
                        f"Marine One LOCAL: {cs_upper or icao_hex} "
                        f"{distance_nm:.1f}nm | {alt_str}",
                        [], [cs_upper or icao_hex],
                    )
                except Exception as e:
                    log.debug("marine_one_local DB write: %s", e)
                log.info("Marine One LOCAL: %s %.1fnm alt=%s", cs_upper, distance_nm, altitude_ft)

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
                            try:
                                msg = json.loads(line)
                                self._queue.put_nowait(msg)
                            except (json.JSONDecodeError, queue.Full):
                                pass
            except Exception as e:
                log.warning("ACARS router connection error: %s — retrying in 15s", e)
            finally:
                self._connected.clear()
            if not self._stop.is_set():
                self._stop.wait(15)


def _process_acars_message(msg: dict) -> None:
    """Parse one ACARS message dict and write to DB; fire watchlist alert if matched."""
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
    raw = json.dumps(msg)

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
    try:
        entries = get_active_entries(entry_type="flight")
        for entry in entries:
            ident = entry["identifier"].upper()
            flight_upper = (flight or "").upper()
            tail_upper = (tail or "").upper()
            if (flight_upper and flight_upper == ident) or \
               (tail_upper and tail_upper == ident):
                watchlist_hit = 1
                watchlist_entry_id = entry["id"]
                if msg_type:
                    summary = (f"{ident} ACARS {msg_type}"
                               + (f": {msg_text[:80]}" if msg_text else ""))
                    watchlist_event_hit(
                        entry["id"], summary,
                        {"watchlist_trigger": f"acars_{msg_type.lower()}",
                         "identifier": ident, "label": label,
                         "msg_type": msg_type, "tail": tail,
                         "icao_hex": icao_hex, "msg_text": msg_text,
                         "source": "acars"},
                        priority=4 if msg_type in ("OFF", "ON", "IN") else 3,
                    )
                break
    except Exception as e:
        log.debug("acars watchlist match: %s", e)

    try:
        db.insert_acars_message(
            received_at=now_iso, freq_mhz=freq_mhz, icao_hex=icao_hex,
            tail=tail, flight=flight, msg_type=msg_type, label=label,
            block_id=block_id, ack=ack, mode=mode, msg_text=msg_text,
            raw=raw, watchlist_hit=watchlist_hit,
            watchlist_entry_id=watchlist_entry_id,
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
        self._acars_reader: Optional[_AcarsReader] = None
        self._last_uf_poll = 0.0
        self._last_acars_drain = 0.0
        self._last_uf_hb = 0.0
        self._last_acars_hb = 0.0

    def _start_acars_reader(self) -> None:
        if self._acars_reader is None or not self._acars_reader.is_alive():
            self._acars_reader = _AcarsReader(
                ACARS_ROUTER_HOST, ACARS_ROUTER_PORT, self._acars_queue)
            self._acars_reader.start()

    def run_forever(self) -> None:
        """Main loop. Runs until the process exits."""
        self._start_acars_reader()
        log.info("LocalAirspaceMonitor started (UF=%s, ACARS=%s:%d)",
                 ULTRAFEEDER_AIRCRAFT_URL or "disabled",
                 ACARS_ROUTER_HOST, ACARS_ROUTER_PORT)

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
                    acars_up = (self._acars_reader is not None and
                                self._acars_reader.is_connected())
                    if acars_up and now - self._last_acars_hb >= HEARTBEAT_INTERVAL:
                        _stamp_heartbeat("acars")
                        self._last_acars_hb = now
                    elif not acars_up:
                        log.debug("ACARS reader not connected — heartbeat not stamped")
                        self._start_acars_reader()
                    if drained:
                        log.debug("ACARS: drained %d message(s)", drained)
                except Exception as e:
                    log.error("ACARS drain error: %s", e)

            time.sleep(2)
