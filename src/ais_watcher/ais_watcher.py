#!/usr/bin/env python3
"""
corporatetraveldc-ais-watcher v1.0
------------------------------------
AIS vessel watcher — monitors UDP stream from local AIS-catcher decoder.
Fires ntfy push via dispatch admin API for any watched MMSI match.

MMSI watchlist is loaded from AIS_STATIC_MMSI env var (comma-separated).
Future: integrate with dispatch vessel watchlist API when implemented.

Data source: AIS-catcher UDP JSON output on AIS_UDP_PORT (default 5006).
AIS-catcher config: add --output json:udp:address=ais-watcher,port=5006
"""

import os
import json
import queue
import socket
import threading
import time
import logging

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DISPATCH_BASE_URL    = os.environ.get("DISPATCH_BASE_URL",    "http://100.94.80.100:8000")
DISPATCH_ADMIN_TOKEN = os.environ.get("DISPATCH_ADMIN_TOKEN", "")
NTFY_TOPIC           = os.environ.get("NTFY_TOPIC",           "flight-alerts")
# Comma-separated MMSI numbers to always watch (9-digit strings)
STATIC_MMSI          = os.environ.get("AIS_STATIC_MMSI",      "")
UDP_HOST             = os.environ.get("AIS_UDP_HOST",          "0.0.0.0")
UDP_PORT             = int(os.environ.get("AIS_UDP_PORT",      "5006"))
UDP_BUFSIZE          = int(os.environ.get("AIS_UDP_BUFSIZE",   "65535"))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [ais-watcher] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)

MSG_QUEUE: queue.Queue = queue.Queue(maxsize=2000)

DISPATCH_SESSION = requests.Session()
DISPATCH_SESSION.headers.update({
    "Authorization": f"Bearer {DISPATCH_ADMIN_TOKEN}",
    "Content-Type": "application/json",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_mmsi(mmsi) -> str:
    return str(mmsi).strip().lstrip("0").zfill(9) if mmsi else ""


def get_watched_mmsi() -> set:
    watched = set()
    if STATIC_MMSI:
        for m in STATIC_MMSI.split(","):
            m = m.strip()
            if m:
                watched.add(normalize_mmsi(m))
    log.info("MMSI watchlist: %d entry(ies) → %s", len(watched), watched or "{none}")
    return watched


def build_ntfy_payload(msg: dict) -> dict:
    mmsi    = str(msg.get("mmsi", "unknown"))
    name    = msg.get("name", "").strip() or "unknown vessel"
    lat     = msg.get("lat") or msg.get("latitude")
    lon     = msg.get("lon") or msg.get("longitude")
    speed   = msg.get("speed") or msg.get("sog")
    course  = msg.get("course") or msg.get("cog")
    status  = msg.get("status", "")
    ship    = msg.get("shiptype") or msg.get("ship_type") or ""

    title = f"AIS: {name} (MMSI {mmsi})"

    parts = []
    if lat is not None and lon is not None:
        parts.append(f"Pos: {lat:.4f}N {lon:.4f}W")
    if speed is not None:
        parts.append(f"SOG: {speed}kts")
    if course is not None:
        parts.append(f"COG: {course}°")
    if status:
        parts.append(f"Status: {status}")
    if ship:
        parts.append(f"Type: {ship}")
    body = " | ".join(parts) if parts else "(no position data)"

    return {
        "topic":    NTFY_TOPIC,
        "priority": 3,
        "title":    title,
        "message":  body,
    }


def fire_ntfy(payload: dict):
    try:
        resp = DISPATCH_SESSION.post(
            f"{DISPATCH_BASE_URL}/admin/push-alert",
            json=payload,
            timeout=10,
        )
        if resp.status_code in (200, 204):
            log.info("ntfy → %s | %s", payload["topic"], payload["title"])
        else:
            log.warning("ntfy HTTP %s: %s", resp.status_code, resp.text[:80])
    except Exception as exc:
        log.error("ntfy error: %s", exc)


# ---------------------------------------------------------------------------
# UDP listener
# ---------------------------------------------------------------------------

def udp_listener_thread():
    """
    Receives AIS JSON datagrams from AIS-catcher.
    AIS-catcher output flag: --output json:udp:address=ais-watcher,port=5006
    Each datagram may be a single JSON object or newline-delimited.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((UDP_HOST, UDP_PORT))
    log.info("UDP listener bound to %s:%d", UDP_HOST, UDP_PORT)

    while True:
        try:
            data, _ = sock.recvfrom(UDP_BUFSIZE)
            raw = data.decode("utf-8", errors="replace").strip()
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    # AIS-catcher sometimes wraps messages in an array
                    if isinstance(obj, list):
                        for item in obj:
                            if isinstance(item, dict):
                                MSG_QUEUE.put_nowait(item)
                    elif isinstance(obj, dict):
                        MSG_QUEUE.put_nowait(obj)
                except json.JSONDecodeError:
                    log.debug("Non-JSON UDP: %s", line[:80])
        except Exception as exc:
            log.error("UDP recv error: %s", exc)
            time.sleep(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

WATCHLIST_REFRESH_INT = int(os.environ.get("WATCHLIST_REFRESH_INTERVAL", "300"))

def main():
    log.info("ais-watcher v1.0 starting")
    log.info("Dispatch: %s", DISPATCH_BASE_URL)
    log.info("UDP:      %s:%d", UDP_HOST, UDP_PORT)
    log.info("ntfy:     %s", NTFY_TOPIC)

    threading.Thread(target=udp_listener_thread, daemon=True, name="udp").start()

    watched           = get_watched_mmsi()
    last_refresh      = time.monotonic()
    seen: set         = set()

    while True:
        if time.monotonic() - last_refresh >= WATCHLIST_REFRESH_INT:
            watched      = get_watched_mmsi()
            last_refresh = time.monotonic()

        try:
            msg = MSG_QUEUE.get(timeout=5)
        except queue.Empty:
            continue

        mmsi_raw = msg.get("mmsi")
        if not mmsi_raw:
            continue

        mmsi = normalize_mmsi(mmsi_raw)

        # Deduplicate: AIS broadcasts frequently — suppress repeats within 5 min
        dedup_key = mmsi
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        # Clear seen set every 5 min to allow re-alerts for moving vessels
        if len(seen) > 1000:
            seen.clear()

        if mmsi in watched:
            payload = build_ntfy_payload(msg)
            name = msg.get("name", "").strip() or mmsi
            log.info("MATCH MMSI %s (%s) — %s",
                     mmsi, name,
                     f"Pos: {msg.get('lat')},{msg.get('lon')}" if msg.get("lat") else "no pos")
            fire_ntfy(payload)


if __name__ == "__main__":
    main()
