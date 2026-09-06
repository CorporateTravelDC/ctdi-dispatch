#!/usr/bin/env python3
"""
corporatetraveldc-utm-watcher v1.0 (2026-08-30)
-------------------------------------------------
UTM / drone Remote ID watcher -- dual-source, modeled explicitly on the
two existing watcher patterns in this repo:

  placement  = ais_watcher (small standalone script, own Containerfile,
               .disabled quadlet until the receiver hardware exists,
               static env-var watchlist as interim override, ntfy via the
               dispatch admin API)
  connection = acars_watcher (local UDP listener thread + REST poller
               thread(s) feeding one queue; watchlist synced from the
               dispatch API on an interval with static env pins on top)

Sources:
  1. UDP listener on UTM_UDP_PORT (default 5007) -- the DEFAULT/primary
     source: JSON lines from a local OpenDroneID-shaped decoder (ASTM
     F3411 Remote ID over Bluetooth/WiFi, e.g. an opendroneid receiver
     feeding JSON over UDP the same way AIS-catcher and acarsdec do).
     ⚠️ SCHEMA CAVEAT: no OpenDroneID receiver exists on this box yet and
     no vendored reference to its message shape exists anywhere in this
     repo (verified 2026-08-30), so the parser below is DEFENSIVE
     best-effort: it extracts the fields ASTM F3411 mandates a Remote ID
     broadcast to carry (UAS ID -- serial or session ID -- position,
     altitude, operator/pilot location) under every field-name variant
     the common open-source receivers are known to emit, logs+skips any
     shape it doesn't recognize, and never crashes on an unexpected
     payload. Expect to tighten extract_uas_id()/build_ntfy_payload()
     against real captures once a receiver is attached.
  2. USS API REST poller -- STUB, inert by default: idles (does not
     error) until both USS_API_BASE and USS_API_KEY are set, mirroring
     how acars_watcher's airframes.io poller shipped and ran for weeks
     before its real credential existed. No endpoint is invented here --
     there is no default USS_API_BASE, and the poll body is a clearly
     marked placeholder to be filled in against the real provider's
     contract when one is provisioned.

Watchlist: synced from the dispatch API's entry_type=="drone" entries
(added to shared/watchlist.py + permanent_drones.json the same pass) every
WATCHLIST_REFRESH_INTERVAL seconds, same as acars_watcher syncs
entry_type=="flight"; UTM_STATIC_IDS env var pins additional UAS IDs on
top, same as ACARS_STATIC_REGS / AIS_STATIC_MMSI.

Fires ntfy push via the dispatch admin API for any watched UAS ID match.
"""

import os
import json
import queue
import socket
import threading
import time
import logging
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DISPATCH_BASE_URL     = os.environ.get("DISPATCH_BASE_URL",    "http://100.x.x.x:8000")
DISPATCH_ADMIN_TOKEN  = os.environ.get("DISPATCH_ADMIN_TOKEN", "")
NTFY_TOPIC            = os.environ.get("NTFY_TOPIC",           "flight-alerts")

# Comma-separated UAS IDs (CTA-2063-A serial numbers or session IDs) to
# always watch, on top of the synced entry_type=="drone" watchlist.
STATIC_IDS            = os.environ.get("UTM_STATIC_IDS",       "")

UDP_HOST              = os.environ.get("UTM_UDP_HOST",         "0.0.0.0")
UDP_PORT              = int(os.environ.get("UTM_UDP_PORT",     "5007"))
UDP_BUFSIZE           = int(os.environ.get("UTM_UDP_BUFSIZE",  "65535"))

# USS API -- BOTH deliberately empty by default: the poller thread idles
# (log-and-sleep, never errors) until a real provider base URL and key are
# provisioned. Do not invent values for these.
USS_API_BASE          = os.environ.get("USS_API_BASE",         "")
USS_API_KEY           = os.environ.get("USS_API_KEY",          "")
USS_POLL_INTERVAL     = int(os.environ.get("USS_POLL_INTERVAL", "60"))

WATCHLIST_REFRESH_INT = int(os.environ.get("WATCHLIST_REFRESH_INTERVAL", "300"))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [utm-watcher] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)

# Shared message queue -- both source threads push dicts here
MSG_QUEUE: queue.Queue = queue.Queue(maxsize=2000)

# Shared watched IDs -- main() owns writes; the USS poller thread reads
# (same lock pattern as acars_watcher's _WATCHED_REGS)
_WATCHED_LOCK: threading.Lock = threading.Lock()
_WATCHED_IDS: set = set()

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "corporatetraveldc-utm-watcher/1.0"})

DISPATCH_SESSION = requests.Session()
DISPATCH_SESSION.headers.update({
    "Authorization": f"Bearer {DISPATCH_ADMIN_TOKEN}",
    "Content-Type":  "application/json",
})


# ---------------------------------------------------------------------------
# Normalisation / defensive field extraction
# ---------------------------------------------------------------------------

def normalize_uas_id(uas_id) -> str:
    """CTA-2063-A serials are upper-case alphanumeric; session IDs are
    opaque strings. Case/whitespace/dash-insensitive comparison is the
    safest equality across receiver implementations."""
    if uas_id is None:
        return ""
    return str(uas_id).strip().upper().replace("-", "")


# Field-name candidates, most-specific first. ASTM F3411's Basic ID
# message carries the UAS ID; the common open-source receivers emit it
# under one of these (best-effort list -- see the module docstring's
# schema caveat).
_UAS_ID_KEYS = ("uas_id", "serial_number", "serial", "id_str", "basic_id",
                "drone_id", "uasid", "id")
_LAT_KEYS  = ("lat", "latitude", "drone_lat")
_LON_KEYS  = ("lon", "lng", "longitude", "drone_lon")
_ALT_KEYS  = ("alt", "altitude", "geodetic_altitude", "alt_geodetic",
              "height", "altitude_geo")
_OP_LAT_KEYS = ("operator_lat", "operator_latitude", "pilot_lat",
                "operator_location_lat")
_OP_LON_KEYS = ("operator_lon", "operator_longitude", "pilot_lon",
                "operator_location_lon")


def _first_key(msg: dict, keys) -> Optional[object]:
    for k in keys:
        v = msg.get(k)
        if v is not None and v != "":
            return v
    return None


def extract_uas_id(msg: dict) -> str:
    """Pull a UAS ID out of whatever shape the decoder emits. Checks the
    top level, then one level of common sub-object nesting ("basic_id" /
    "BasicID" / "system" style containers). Returns "" when nothing
    ID-shaped is present -- the caller logs+skips, never raises."""
    v = _first_key(msg, _UAS_ID_KEYS)
    if v is not None and not isinstance(v, (dict, list)):
        return str(v).strip()
    for container_key in ("basic_id", "BasicID", "basicId", "id",
                          "identification", "uas"):
        sub = msg.get(container_key)
        if isinstance(sub, dict):
            v = _first_key(sub, _UAS_ID_KEYS)
            if v is not None and not isinstance(v, (dict, list)):
                return str(v).strip()
    return ""


def _extract_float(msg: dict, keys, containers=("location", "Location",
                                                "position", "system",
                                                "System")) -> Optional[float]:
    v = _first_key(msg, keys)
    if v is None:
        for ck in containers:
            sub = msg.get(ck)
            if isinstance(sub, dict):
                v = _first_key(sub, keys)
                if v is not None:
                    break
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def build_ntfy_payload(uas_id: str, msg: dict, source: str) -> dict:
    lat    = _extract_float(msg, _LAT_KEYS)
    lon    = _extract_float(msg, _LON_KEYS)
    alt    = _extract_float(msg, _ALT_KEYS)
    op_lat = _extract_float(msg, _OP_LAT_KEYS)
    op_lon = _extract_float(msg, _OP_LON_KEYS)
    op_id  = msg.get("operator_id") or msg.get("operator") or ""
    desc   = msg.get("description") or msg.get("self_id") or ""

    title = f"UAS: {uas_id} [{source}]"

    parts = []
    if lat is not None and lon is not None:
        parts.append(f"Pos: {lat:.5f},{lon:.5f}")
    if alt is not None:
        parts.append(f"Alt: {alt:.0f}m")
    if op_lat is not None and op_lon is not None:
        parts.append(f"Operator: {op_lat:.5f},{op_lon:.5f}")
    if op_id and not isinstance(op_id, (dict, list)):
        parts.append(f"OpID: {op_id}")
    if desc and not isinstance(desc, (dict, list)):
        parts.append(f"Desc: {str(desc)[:80]}")
    body = " | ".join(parts) if parts else "(no position data)"

    return {
        "topic":    NTFY_TOPIC,
        # Priority 4: a watched UAS actually broadcasting nearby is a
        # security-relevant event (counter-UAS interest), one notch above
        # the AIS watcher's routine vessel-position 3.
        "priority": 4,
        "title":    title,
        "message":  body,
    }


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def get_watched_ids() -> set:
    """Static UTM_STATIC_IDS pins + dispatch watchlist entry_type=="drone"
    entries (same sync shape as acars_watcher's entry_type=="flight"
    sync; identifier = the broadcast UAS ID)."""
    watched = set()
    if STATIC_IDS:
        for s in STATIC_IDS.split(","):
            s = s.strip()
            if s:
                watched.add(normalize_uas_id(s))
    try:
        resp = DISPATCH_SESSION.get(
            f"{DISPATCH_BASE_URL}/api/v1/watchlist", timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            for entry in data.get("entries", []):
                if entry.get("entry_type") == "drone":
                    ident = entry.get("identifier", "")
                    if ident:
                        watched.add(normalize_uas_id(ident))
            log.info("Watchlist: %d UAS ID(s) → %s", len(watched), watched or "{none}")
        else:
            log.warning("Watchlist HTTP %s", resp.status_code)
    except Exception as exc:
        log.error("Watchlist error: %s", exc)
    return watched


def _sync_shared_watched(ids: set):
    with _WATCHED_LOCK:
        _WATCHED_IDS.clear()
        _WATCHED_IDS.update(ids)


# ---------------------------------------------------------------------------
# ntfy push
# ---------------------------------------------------------------------------

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
# Thread 1: UDP listener (local OpenDroneID-shaped decoder feed) -- PRIMARY
# ---------------------------------------------------------------------------

def udp_listener_thread():
    """
    Receives JSON datagrams from a local Remote ID decoder. Each datagram
    may be a single JSON object, a JSON array, or newline-delimited JSON
    -- same tolerant framing as ais_watcher's AIS-catcher listener, since
    the real decoder's framing can't be verified until hardware exists.
    Anything non-JSON or non-dict-shaped is logged at DEBUG and dropped.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((UDP_HOST, UDP_PORT))
    log.info("UDP listener bound to %s:%d", UDP_HOST, UDP_PORT)

    while True:
        try:
            data, addr = sock.recvfrom(UDP_BUFSIZE)
            raw = data.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("UDP non-JSON from %s: %s", addr, line[:80])
                    continue
                items = obj if isinstance(obj, list) else [obj]
                for item in items:
                    if isinstance(item, dict):
                        item["_source"] = "LOCAL"
                        try:
                            MSG_QUEUE.put_nowait(item)
                        except queue.Full:
                            pass
                    else:
                        log.debug("UDP non-dict payload skipped: %r",
                                  str(item)[:80])
        except Exception as exc:
            log.error("UDP recv error: %s", exc)
            time.sleep(1)


# ---------------------------------------------------------------------------
# Thread 2: USS API REST poller -- STUB, inert until provisioned
# ---------------------------------------------------------------------------

def uss_poller_thread():
    """
    Future USS (UAS Service Supplier) API poller. Deliberately inert:
    with USS_API_BASE/USS_API_KEY unset (the default -- neither exists
    yet) it logs once and idles forever, exactly the posture
    acars_watcher's jumpseat poller takes with no token and the
    airframes.io poller took for the weeks it ran before its real
    credential existed. The thread structure, session, and queue wiring
    are real so provisioning is an env-var change plus filling in the
    request/response mapping below against the actual provider contract
    -- NOT a code restructure.
    """
    if not USS_API_BASE or not USS_API_KEY:
        log.info("USS poller: no USS_API_BASE/USS_API_KEY configured — "
                 "thread idle (expected: no USS provider is provisioned yet)")
        while True:
            time.sleep(3600)

    SESSION.headers.update({"Authorization": f"Bearer {USS_API_KEY}"})
    log.info("USS poller active — %s, interval %ds", USS_API_BASE, USS_POLL_INTERVAL)

    while True:
        with _WATCHED_LOCK:
            ids = set(_WATCHED_IDS)
        if not ids:
            log.debug("USS poller: watchlist empty — sleeping")
            time.sleep(USS_POLL_INTERVAL)
            continue
        # ── PLACEHOLDER ──────────────────────────────────────────────
        # No real USS provider contract exists to code against (and none
        # is invented here). When one is provisioned, implement its
        # actual query here (per-UAS-ID or area subscription, per the
        # provider's API), normalize each result to the same dict shape
        # the UDP parser consumes (extract_uas_id()-compatible), stamp
        # {"_source": "USS"}, and MSG_QUEUE.put_nowait() it -- mirroring
        # acars_watcher._normalize_jumpseat(). Handle 401 (idle 1h) and
        # 429 (Retry-After) the same way that poller does.
        log.debug("USS poller: configured but request mapping not yet "
                  "implemented for this provider — idling this cycle")
        time.sleep(USS_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Main: message dispatch loop
# ---------------------------------------------------------------------------

# Per-UAS-ID alert dedup window: Remote ID broadcasts arrive ~1/s, so a
# watched drone would otherwise page continuously for its whole flight.
ALERT_DEDUP_SECS = int(os.environ.get("UTM_ALERT_DEDUP_SECS", "300"))


def main():
    log.info("utm-watcher v1.0 starting")
    log.info("Dispatch: %s", DISPATCH_BASE_URL)
    log.info("UDP:      %s:%d", UDP_HOST, UDP_PORT)
    log.info("USS:      %s", USS_API_BASE or "(not configured — poller idle)")
    log.info("ntfy:     %s", NTFY_TOPIC)

    threading.Thread(target=udp_listener_thread, daemon=True, name="udp").start()
    threading.Thread(target=uss_poller_thread,   daemon=True, name="uss").start()

    watched           = get_watched_ids()
    _sync_shared_watched(watched)
    last_refresh      = time.monotonic()
    last_alert: dict  = {}   # normalized uas_id -> monotonic ts of last push

    log.info("Main loop running")
    while True:
        if time.monotonic() - last_refresh >= WATCHLIST_REFRESH_INT:
            watched      = get_watched_ids()
            _sync_shared_watched(watched)
            last_refresh = time.monotonic()

        try:
            msg = MSG_QUEUE.get(timeout=5)
        except queue.Empty:
            continue

        try:
            uas_id_raw = extract_uas_id(msg)
        except Exception as exc:  # defensive: parser must never kill the loop
            log.debug("UAS ID extraction failed (%s): %r", exc, str(msg)[:120])
            continue
        if not uas_id_raw:
            log.debug("Unrecognized message shape (no UAS ID): %r",
                      str(msg)[:120])
            continue

        uas_id = normalize_uas_id(uas_id_raw)
        if uas_id not in watched:
            continue

        now = time.monotonic()
        if now - last_alert.get(uas_id, -ALERT_DEDUP_SECS) < ALERT_DEDUP_SECS:
            continue
        last_alert[uas_id] = now
        if len(last_alert) > 1000:
            last_alert.clear()

        source  = msg.get("_source", "UNKNOWN")
        payload = build_ntfy_payload(uas_id_raw, msg, source)
        log.info("MATCH UAS %s [%s] — %s", uas_id_raw, source,
                 payload["message"][:80])
        fire_ntfy(payload)


if __name__ == "__main__":
    main()
