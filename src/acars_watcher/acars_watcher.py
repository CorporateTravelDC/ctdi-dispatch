#!/usr/bin/env python3
"""
corporatetraveldc-acars-watcher v3.0
-------------------------------------
Triple-source ACARS/VDL2 watcher:
  1. UDP listener on ACARS_UDP_PORT (local acarsdec / dumpvdl2 decoders)
  2. airframes.io REST polling (aggregate fallback / supplement; auth optional)
  3. ACARS Drama Jumpseat REST polling (per-registration; Privileged Access)

Fires ntfy push via dispatch admin API for any watched registration match.
Signals (ECS, medical, divert, fuel, smoke, EDCT) are classified from message
text and reflected in push priority and title tags.

Watchlist synced from dispatch OOOI watchlist every WATCHLIST_REFRESH_INTERVAL
seconds.  Static registrations can be pinned via ACARS_STATIC_REGS env var.

Secret: JUMPSEAT_API_KEY env var  OR  ~/.secrets/jumpseat.key
"""

import os
import json
import pathlib
import queue
import re
import socket
import threading
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DISPATCH_BASE_URL     = os.environ.get("DISPATCH_BASE_URL",       "http://100.x.x.x:8000")
DISPATCH_ADMIN_TOKEN  = os.environ.get("DISPATCH_ADMIN_TOKEN",    "")

AIRFRAMES_API_BASE    = os.environ.get("AIRFRAMES_API_BASE",      "https://api.airframes.io/v1")
AIRFRAMES_API_KEY     = os.environ.get("AIRFRAMES_API_KEY",       "")
POLL_INTERVAL         = int(os.environ.get("POLL_INTERVAL",       "60"))

JUMPSEAT_API_BASE     = os.environ.get("JUMPSEAT_API_BASE",       "https://api.jumpseat.acarsdrama.com/v1")
JUMPSEAT_POLL_INT     = int(os.environ.get("JUMPSEAT_POLL_INTERVAL", "90"))

WATCHLIST_REFRESH_INT = int(os.environ.get("WATCHLIST_REFRESH_INTERVAL", "300"))
NTFY_TOPIC            = os.environ.get("NTFY_TOPIC",              "flight-alerts")
STATIC_REGS           = os.environ.get("ACARS_STATIC_REGS",       "")
UDP_HOST              = os.environ.get("ACARS_UDP_HOST",          "0.0.0.0")
UDP_PORT              = int(os.environ.get("ACARS_UDP_PORT",      "5005"))
UDP_BUFSIZE           = int(os.environ.get("ACARS_UDP_BUFSIZE",   "65535"))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [acars-watcher] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)

# Shared message queue — all three source threads push dicts here
MSG_QUEUE: queue.Queue = queue.Queue(maxsize=5000)

# Shared watched registrations — main() owns writes; poller threads read
_WATCHED_LOCK: threading.Lock = threading.Lock()
_WATCHED_REGS: set = set()

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "corporatetraveldc-acars-watcher/3.0"})
if AIRFRAMES_API_KEY:
    SESSION.headers.update({"Authorization": f"Bearer {AIRFRAMES_API_KEY}"})

DISPATCH_SESSION = requests.Session()
DISPATCH_SESSION.headers.update({
    "Authorization": f"Bearer {DISPATCH_ADMIN_TOKEN}",
    "Content-Type":  "application/json",
})


# ---------------------------------------------------------------------------
# Jumpseat token resolution
# ---------------------------------------------------------------------------

def _resolve_jumpseat_token() -> Optional[str]:
    env = os.environ.get("JUMPSEAT_API_KEY", "").strip()
    if env:
        return env
    secret = pathlib.Path.home() / ".secrets" / "jumpseat.key"
    if secret.exists():
        return secret.read_text().strip()
    return None


_JUMPSEAT_TOKEN: Optional[str] = _resolve_jumpseat_token()

JUMPSEAT_SESSION = requests.Session()
JUMPSEAT_SESSION.headers.update({
    "User-Agent": "corporatetraveldc-acars-watcher/3.0",
    "Accept":     "application/json",
})
if _JUMPSEAT_TOKEN:
    JUMPSEAT_SESSION.headers.update({"Authorization": f"Bearer {_JUMPSEAT_TOKEN}"})


# ---------------------------------------------------------------------------
# Signal classification
# ---------------------------------------------------------------------------

_SIGNAL_PATTERNS: list[tuple[str, list[str], int]] = [
    # (signal_label, keywords, ntfy_priority_override)
    ("MECH_ECS",    ["temp", "sweating", "pack", "bleed", "ecs",
                     "cant control", "temperature", "pressur"],     4),
    ("MECH_ENGINE", ["engine", " eng ", "shutdown", "fire loop",
                     "oil press", "vibr", "flame out"],             5),
    ("MEDICAL",     ["medical", " ill ", "unconscious", "defib",
                     "emt", "doctor", "physician", "cardiac",
                     "seiz", "unrespons"],                          5),
    ("DIVERT",      ["divert", "dvrt", "alternate", "altn ",
                     "returning to"],                               4),
    ("FUEL",        ["min fuel", "minimum fuel", "fuel state",
                     "endurance", "low fuel"],                      4),
    ("SMOKE_FUMES", ["smoke", "fumes", "odor", "smell", "burning"], 5),
    ("EDCT",        ["edct", "expect departure", "wheels up",
                     "ctot", "expect clearance"],                   3),
]

_EDCT_RE = re.compile(
    r"(?:edct|wheels up|expect departure|ctot)[^\d]{0,20}(\d{2}:\d{2}(?:Z|UTC)?|\d{4}Z?)",
    re.IGNORECASE,
)


def classify_text(text: str) -> tuple[list[str], int, Optional[str]]:
    """
    Returns (signals, priority, edct_raw).
    priority is the highest override across matched signals, floored at 3.
    """
    if not text:
        return [], 3, None
    lower = text.lower()
    signals = []
    priority = 3
    for label, keywords, pri in _SIGNAL_PATTERNS:
        if any(kw in lower for kw in keywords):
            signals.append(label)
            priority = max(priority, pri)
    edct_m = _EDCT_RE.search(text)
    edct_raw = edct_m.group(1) if edct_m else None
    return signals, priority, edct_raw


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def normalize_reg(reg: str) -> str:
    return reg.upper().replace("-", "").strip() if reg else ""


def extract_reg_from_msg(msg: dict) -> str:
    for key in ("tail", "registration", "reg", "aircraft_reg", "acars.reg"):
        v = msg.get(key, "")
        if v:
            return str(v).strip()
    acars = msg.get("acars") or {}
    if isinstance(acars, dict):
        v = acars.get("reg", "") or acars.get("tail", "")
        if v:
            return str(v).strip()
    return ""


def build_ntfy_payload(reg: str, msg: dict, source: str) -> dict:
    tail   = extract_reg_from_msg(msg) or reg
    flight = (msg.get("flight")
              or msg.get("flightNumber")
              or (msg.get("acars") or {}).get("flight")
              or "")
    label  = (msg.get("label")
              or (msg.get("acars") or {}).get("label")
              or msg.get("type")
              or "ACARS")
    text   = (msg.get("cleanedText")
              or msg.get("text")
              or (msg.get("acars") or {}).get("msg_text")
              or msg.get("message")
              or "").strip()
    freq   = str(msg.get("freq") or msg.get("frequency") or "")
    station = msg.get("station") or {}
    sta_loc = (msg.get("stationLocation")
               or (station.get("location") if isinstance(station, dict) else "")
               or "")
    sta_id  = (msg.get("stationIdentifier")
               or (station.get("ident", "") if isinstance(station, dict) else str(station))
               or "")

    signals, priority, edct_raw = classify_text(text)
    sig_tag = f" [{','.join(signals)}]" if signals else ""

    if flight:
        title = f"{tail} ({flight}) — {label}{sig_tag} [{source}]"
    else:
        title = f"{tail} — {label}{sig_tag} [{source}]"

    parts = []
    if text:
        parts.append(text[:500])
    if edct_raw:
        parts.append(f"EDCT: {edct_raw}")
    meta = []
    if freq:
        meta.append(f"Freq: {freq}")
    if sta_loc:
        meta.append(f"Stn: {sta_loc}")
    elif sta_id:
        meta.append(f"Stn: {sta_id}")
    if meta:
        parts.append(" | ".join(meta))
    body = "\n".join(parts) if parts else "(no message text)"

    return {
        "topic":    NTFY_TOPIC,
        "priority": priority,
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
            for entry in data.get("entries", []):
                if entry.get("entry_type") == "flight":
                    ident = entry.get("identifier", "")
                    if ident:
                        watched.add(normalize_reg(ident))
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


def _sync_shared_watched(regs: set):
    with _WATCHED_LOCK:
        _WATCHED_REGS.clear()
        _WATCHED_REGS.update(regs)


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
    acarsdec:  -j udp://127.0.0.1:5005
    dumpvdl2:  --output decoded:json:udp:address=acars-watcher,port=5005
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
# Thread 2: airframes.io REST poller (global stream, auth optional)
# ---------------------------------------------------------------------------

def rest_poller_thread():
    """Polls airframes.io /v1/messages every POLL_INTERVAL seconds."""
    last_poll = datetime.now(timezone.utc) - timedelta(seconds=POLL_INTERVAL + 10)

    while True:
        since     = last_poll
        last_poll = datetime.now(timezone.utc)

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
                msgs = (data if isinstance(data, list)
                        else data.get("messages", data.get("data", [])))
                log.debug("REST airframes: %d msg(s) since %s",
                          len(msgs), since.isoformat())
                for msg in msgs:
                    if isinstance(msg, dict):
                        msg["_source"] = "AIRFRAMES"
                        try:
                            MSG_QUEUE.put_nowait(msg)
                        except queue.Full:
                            pass
            elif resp.status_code == 401:
                log.warning("REST airframes: 401 — API key required, thread idle")
                time.sleep(3600)
            elif resp.status_code == 429:
                retry = int(resp.headers.get("Retry-After", 120))
                log.warning("REST airframes: rate-limited — sleeping %ds", retry)
                time.sleep(retry)
            else:
                log.warning("REST airframes: HTTP %s", resp.status_code)
        except Exception as exc:
            log.error("REST airframes poll error: %s", exc)

        time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Thread 3: ACARS Drama Jumpseat REST poller (per-registration)
# ---------------------------------------------------------------------------

def jumpseat_poller_thread():
    """
    Polls ACARS Drama Jumpseat /v1/messages/search?registration=<REG>
    for each currently watched registration every JUMPSEAT_POLL_INT seconds.

    Requires JUMPSEAT_API_KEY env var or ~/.secrets/jumpseat.key.
    Silently idles if no token is available.
    """
    if not _JUMPSEAT_TOKEN:
        log.warning("Jumpseat: no token found — thread idle "
                    "(set JUMPSEAT_API_KEY or write ~/.secrets/jumpseat.key)")
        return

    log.info("Jumpseat poller active — interval %ds", JUMPSEAT_POLL_INT)
    seen_ids: set = set()

    while True:
        with _WATCHED_LOCK:
            regs = set(_WATCHED_REGS)

        if not regs:
            log.debug("Jumpseat: watchlist empty — sleeping")
            time.sleep(JUMPSEAT_POLL_INT)
            continue

        for reg in regs:
            try:
                resp = JUMPSEAT_SESSION.get(
                    f"{JUMPSEAT_API_BASE}/messages/search",
                    params={"registration": reg, "limit": "20", "source": "messages"},
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    items = (data.get("items", data)
                             if isinstance(data, dict) else data)
                    for item in items:
                        item_id = str(item.get("id", ""))
                        if item_id and item_id in seen_ids:
                            continue
                        if item_id:
                            seen_ids.add(item_id)
                        # Normalize to internal format and enqueue
                        msg = _normalize_jumpseat(item, reg)
                        try:
                            MSG_QUEUE.put_nowait(msg)
                        except queue.Full:
                            log.warning("Jumpseat: MSG_QUEUE full — dropping")
                elif resp.status_code == 401:
                    log.error("Jumpseat: 401 — token invalid or expired")
                    time.sleep(3600)
                    break
                elif resp.status_code == 429:
                    retry = int(resp.headers.get("Retry-After", 60))
                    log.warning("Jumpseat: rate-limited — sleeping %ds", retry)
                    time.sleep(retry)
                    break
                else:
                    log.warning("Jumpseat: HTTP %s for %s", resp.status_code, reg)
            except Exception as exc:
                log.error("Jumpseat poll error (%s): %s", reg, exc)

            # Small inter-registration gap to avoid hammering the API
            time.sleep(2)

        # Trim seen_ids to avoid unbounded growth
        if len(seen_ids) > 50000:
            seen_ids = set(list(seen_ids)[25000:])

        time.sleep(JUMPSEAT_POLL_INT)


def _normalize_jumpseat(item: dict, reg: str) -> dict:
    """
    Map a Jumpseat /v1/messages/search response item to the
    internal message dict format consumed by the main dispatch loop.
    """
    return {
        # Identity
        "id":              item.get("id"),
        "_source":         "JUMPSEAT",
        # Registration / flight
        "registration":    item.get("registration", reg),
        "tail":            item.get("registration", reg),
        "flight":          item.get("flightNumber", ""),
        "flightNumber":    item.get("flightNumber", ""),
        # Message content
        "label":           item.get("label", item.get("protocol", "ACARS")),
        "text":            item.get("cleanedText") or item.get("text") or "",
        "cleanedText":     item.get("cleanedText", ""),
        # Metadata
        "timestamp":       item.get("timestamp", ""),
        "protocol":        item.get("protocol", ""),
        "direction":       item.get("direction", ""),
        "directionLabel":  item.get("directionLabel", ""),
        "stationLocation": item.get("stationLocation", ""),
        "stationIdentifier": item.get("stationIdentifier", ""),
        "categories":      item.get("categories", []),
        "isAutomated":     item.get("isAutomated", False),
        "messageKind":     item.get("messageKind", ""),
    }


# ---------------------------------------------------------------------------
# Main: message dispatch loop
# ---------------------------------------------------------------------------

def main():
    log.info("acars-watcher v3.0 starting")
    log.info("Dispatch:    %s", DISPATCH_BASE_URL)
    log.info("Airframes:   %s  auth=%s",
             AIRFRAMES_API_BASE, "key" if AIRFRAMES_API_KEY else "none")
    log.info("Jumpseat:    %s  auth=%s",
             JUMPSEAT_API_BASE, "key" if _JUMPSEAT_TOKEN else "MISSING")
    log.info("UDP:         %s:%d", UDP_HOST, UDP_PORT)
    log.info("Poll:        airframes=%ds  jumpseat=%ds  watchlist=%ds",
             POLL_INTERVAL, JUMPSEAT_POLL_INT, WATCHLIST_REFRESH_INT)
    log.info("ntfy topic:  %s", NTFY_TOPIC)

    threading.Thread(target=udp_listener_thread,  daemon=True, name="udp").start()
    threading.Thread(target=rest_poller_thread,   daemon=True, name="airframes").start()
    threading.Thread(target=jumpseat_poller_thread, daemon=True, name="jumpseat").start()

    watched           = get_watched_registrations()
    _sync_shared_watched(watched)
    last_watchlist_ts = time.monotonic()
    seen_ids: set     = set()

    log.info("Main loop running")
    while True:
        if time.monotonic() - last_watchlist_ts >= WATCHLIST_REFRESH_INT:
            watched           = get_watched_registrations()
            _sync_shared_watched(watched)
            last_watchlist_ts = time.monotonic()

        try:
            msg = MSG_QUEUE.get(timeout=5)
        except queue.Empty:
            continue

        # Deduplicate by ID
        msg_id = str(msg.get("id", ""))
        if msg_id and msg_id in seen_ids:
            continue
        if msg_id:
            seen_ids.add(msg_id)
            if len(seen_ids) > 20000:
                seen_ids = set(list(seen_ids)[10000:])

        reg_raw = extract_reg_from_msg(msg)
        if not reg_raw:
            continue

        if normalize_reg(reg_raw) in watched:
            source  = msg.get("_source", "UNKNOWN")
            payload = build_ntfy_payload(reg_raw, msg, source)
            signals_text = ""
            signals, _, _ = classify_text(
                msg.get("cleanedText") or msg.get("text") or ""
            )
            if signals:
                signals_text = f" signals={signals}"
            log.info(
                "MATCH [%s] %s%s — %s",
                source, reg_raw, signals_text,
                (msg.get("cleanedText") or msg.get("text") or "")[:80],
            )
            fire_ntfy(payload)


if __name__ == "__main__":
    main()
