#!/usr/bin/env python3
"""
corporatetraveldc-acars-watcher v2.0
-------------------------------------
Dual-source ACARS/VDL2 watcher:
  1. UDP listener on ACARS_UDP_PORT (local acarsdec / dumpvdl2 decoders)
  2. airframes.io REST polling (aggregate fallback / supplement)

Fires ntfy push via dispatch admin API for any watched registration match.
Watchlist synced from dispatch OOOI watchlist every WATCHLIST_REFRESH_INTERVAL seconds.
Static registrations can be pinned via ACARS_STATIC_REGS env var.
"""

import os
import json
import queue
import socket
import threading
import time
import logging
from datetime import datetime, timezone, timedelta

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DISPATCH_BASE_URL     = os.environ.get("DISPATCH_BASE_URL",      "http://100.94.80.100:8000")
DISPATCH_ADMIN_TOKEN  = os.environ.get("DISPATCH_ADMIN_TOKEN",   "")
AIRFRAMES_API_BASE    = os.environ.get("AIRFRAMES_API_BASE",     "https://api.airframes.io/v1")
AIRFRAMES_API_KEY     = os.environ.get("AIRFRAMES_API_KEY",      "")   # optional
POLL_INTERVAL         = int(os.environ.get("POLL_INTERVAL",      "60"))
WATCHLIST_REFRESH_INT = int(os.environ.get("WATCHLIST_REFRESH_INTERVAL", "300"))
NTFY_TOPIC            = os.environ.get("NTFY_TOPIC",             "flight-alerts")
STATIC_REGS           = os.environ.get("ACARS_STATIC_REGS",      "")
# UDP listener for local acarsdec / dumpvdl2 output
UDP_HOST              = os.environ.get("ACARS_UDP_HOST",         "0.0.0.0")
UDP_PORT              = int(os.environ.get("ACARS_UDP_PORT",     "5005"))
UDP_BUFSIZE           = int(os.environ.get("ACARS_UDP_BUFSIZE",  "65535"))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [acars-watcher] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)

# Shared message queue: both UDP and REST threads push dicts here
MSG_QUEUE: queue.Queue = queue.Queue(maxsize=5000)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "corporatetraveldc-acars-watcher/2.0"})
if AIRFRAMES_API_KEY:
    SESSION.headers.update({"Authorization": f"Bearer {AIRFRAMES_API_KEY}"})

DISPATCH_SESSION = requests.Session()
DISPATCH_SESSION.headers.update({
    "Authorization": f"Bearer {DISPATCH_ADMIN_TOKEN}",
    "Content-Type": "application/json",
})


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def normalize_reg(reg: str) -> str:
    return reg.upper().replace("-", "").strip() if reg else ""


def extract_reg_from_msg(msg: dict) -> str:
    """Try multiple field names used by acarsdec, dumpvdl2, airframes.io."""
    for key in ("tail", "registration", "reg", "aircraft_reg", "acars.reg"):
        v = msg.get(key, "")
        if v:
            return str(v).strip()
    # Nested ACARS block (dumpvdl2 format)
    acars = msg.get("acars") or {}
    if isinstance(acars, dict):
        v = acars.get("reg", "") or acars.get("tail", "")
        if v:
            return str(v).strip()
    return ""


def build_ntfy_payload(reg: str, msg: dict, source: str) -> dict:
    tail    = extract_reg_from_msg(msg) or reg
    flight  = msg.get("flight") or (msg.get("acars") or {}).get("flight") or ""
    label   = (msg.get("label")
               or (msg.get("acars") or {}).get("label")
               or msg.get("type")
               or "ACARS")
    text    = (msg.get("text")
               or (msg.get("acars") or {}).get("msg_text")
               or msg.get("message")
               or "").strip()
    freq    = str(msg.get("freq") or msg.get("frequency") or "")
    station = msg.get("station") or {}
    sta_id  = station.get("ident", "") if isinstance(station, dict) else str(station)

    if flight:
        title = f"{tail} ({flight}) — {label} [{source}]"
    else:
        title = f"{tail} — {label} [{source}]"

    parts = []
    if text:
        parts.append(text[:500])
    meta = []
    if freq:
        meta.append(f"Freq: {freq}")
    if sta_id:
        meta.append(f"Stn: {sta_id}")
    if meta:
        parts.append(" | ".join(meta))
    body = "\n".join(parts) if parts else "(no message text)"

    return {
        "topic":    NTFY_TOPIC,
        "priority": 3,
        "title":    title,
        "message":  body,
    }


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def get_watched_registrations() -> set:
    watched = set()
    if STATIC_REGS:
        for r in STATIC_REGS.split(","):
            r = r.strip()
            if r:
                watched.add(normalize_reg(r))
    try:
        resp = DISPATCH_SESSION.get(
            f"{DISPATCH_BASE_URL}/api/v1/watchlist", timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            # API schema v2: entries[].entry_type + entries[].identifier
            for entry in data.get("entries", []):
                if entry.get("entry_type") == "flight":
                    ident = entry.get("identifier", "")
                    if ident:
                        watched.add(normalize_reg(ident))
            # Transient OOOI session schema: sessions[].registration
            for s in data.get("sessions", []):
                if s.get("session_type") == "flight":
                    reg = s.get("registration", "") or s.get("subject", "")
                    if reg:
                        watched.add(normalize_reg(reg))
            log.info("Watchlist: %d reg(s) → %s", len(watched), watched or "{none}")
        else:
            log.warning("Watchlist HTTP %s", resp.status_code)
    except Exception as exc:
        log.error("Watchlist error: %s", exc)
    return watched


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
# Thread 1: UDP listener (local acarsdec / dumpvdl2)
# ---------------------------------------------------------------------------

def udp_listener_thread():
    """
    Listens on UDP_PORT for JSON datagrams from local decoders.
    acarsdec: run with -j udp://127.0.0.1:5005 (or container network equivalent)
    dumpvdl2: run with --output decoded:json:udp:address=acars-watcher,port=5005
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
            # Decoders sometimes send newline-delimited JSON
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if isinstance(msg, dict):
                        msg["_source"] = "LOCAL"
                        MSG_QUEUE.put_nowait(msg)
                except json.JSONDecodeError:
                    log.debug("UDP non-JSON from %s: %s", addr, line[:80])
        except Exception as exc:
            log.error("UDP recv error: %s", exc)
            time.sleep(1)


# ---------------------------------------------------------------------------
# Thread 2: airframes.io REST poller
# ---------------------------------------------------------------------------

def rest_poller_thread():
    """Polls airframes.io /v1/messages every POLL_INTERVAL seconds."""
    last_poll = datetime.now(timezone.utc) - timedelta(seconds=POLL_INTERVAL + 10)

    while True:
        since      = last_poll
        last_poll  = datetime.now(timezone.utc)

        params = {
            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 500,
        }
        try:
            resp = SESSION.get(
                f"{AIRFRAMES_API_BASE}/messages",
                params=params,
                timeout=20,
            )
            if resp.status_code == 200:
                data = resp.json()
                msgs = data if isinstance(data, list) else data.get("messages", data.get("data", []))
                log.debug("REST: %d message(s) since %s", len(msgs), since.isoformat())
                for msg in msgs:
                    if isinstance(msg, dict):
                        msg["_source"] = "AIRFRAMES"
                        try:
                            MSG_QUEUE.put_nowait(msg)
                        except queue.Full:
                            pass
            elif resp.status_code == 429:
                retry = int(resp.headers.get("Retry-After", 120))
                log.warning("REST rate-limited — sleeping %ds", retry)
                time.sleep(retry)
            else:
                log.warning("REST HTTP %s", resp.status_code)
        except Exception as exc:
            log.error("REST poll error: %s", exc)

        time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Main: message dispatch loop
# ---------------------------------------------------------------------------

def main():
    log.info("acars-watcher v2.0 starting")
    log.info("Dispatch:    %s", DISPATCH_BASE_URL)
    log.info("Airframes:   %s", AIRFRAMES_API_BASE)
    log.info("Auth:        %s", "API key" if AIRFRAMES_API_KEY else "public (no key)")
    log.info("UDP:         %s:%d", UDP_HOST, UDP_PORT)
    log.info("Poll:        %ds  Watchlist refresh: %ds", POLL_INTERVAL, WATCHLIST_REFRESH_INT)
    log.info("ntfy topic:  %s", NTFY_TOPIC)

    # Start background threads
    threading.Thread(target=udp_listener_thread, daemon=True, name="udp").start()
    threading.Thread(target=rest_poller_thread,  daemon=True, name="rest").start()

    watched            = get_watched_registrations()
    last_watchlist_ts  = time.monotonic()
    seen_ids: set      = set()

    log.info("Main loop running")
    while True:
        # Refresh watchlist
        if time.monotonic() - last_watchlist_ts >= WATCHLIST_REFRESH_INT:
            watched           = get_watched_registrations()
            last_watchlist_ts = time.monotonic()

        # Drain message queue with a short timeout
        try:
            msg = MSG_QUEUE.get(timeout=5)
        except queue.Empty:
            continue

        # Deduplicate by ID (airframes.io assigns IDs; local msgs may not have one)
        msg_id = str(msg.get("id", ""))
        if msg_id and msg_id in seen_ids:
            continue
        if msg_id:
            seen_ids.add(msg_id)
            if len(seen_ids) > 20000:
                seen_ids = set(list(seen_ids)[10000:])

        # Filter: no registration → skip
        reg_raw = extract_reg_from_msg(msg)
        if not reg_raw:
            continue

        # Match against watchlist
        if normalize_reg(reg_raw) in watched:
            source  = msg.get("_source", "UNKNOWN")
            payload = build_ntfy_payload(reg_raw, msg, source)
            log.info(
                "MATCH [%s] %s — %s | %s",
                source,
                reg_raw,
                msg.get("label") or (msg.get("acars") or {}).get("label") or "?",
                (msg.get("text") or (msg.get("acars") or {}).get("msg_text") or "")[:60],
            )
            fire_ntfy(payload)


if __name__ == "__main__":
    main()
