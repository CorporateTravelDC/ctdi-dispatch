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

Two lists, since 2026-10-16 (operator: keep the LADD list for Jumpseat):
  - the FULL set (GET /api/v1/watchlist entries + sessions, plus
    ACARS_STATIC_REGS) is what the local UDP and airframes.io window
    matches are checked against -- those sources arrive on their own and
    matching them locally sends nothing anywhere;
  - the Jumpseat thread queries a third party BY registration, so it uses
    ONLY GET /api/v1/watchlist/registrations -- the same identifiers with
    every LADD-listed one removed by the dispatch API's production LADD
    filter (this container has no database of its own). If that route is
    unavailable the thread polls nothing and says so; it never falls back
    to the full set, and no filtered registration is ever logged here
    (the route does not return them, and this file logs counts only).

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

# 2026-10-18: message-type flags (acars_flags.py, byte-identical to
# src/common/acars_flags.py). Package import in tests, flat import in the image
# (WORKDIR /app holds acars_watcher.py + acars_flags.py side by side).
try:
    from . import acars_flags
except ImportError:
    import acars_flags  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DISPATCH_BASE_URL     = os.environ.get("DISPATCH_BASE_URL",       "http://100.x.x.x:8000")
DISPATCH_ADMIN_TOKEN  = os.environ.get("DISPATCH_ADMIN_TOKEN",    "")

AIRFRAMES_API_BASE    = os.environ.get("AIRFRAMES_API_BASE",      "https://api.airframes.io/v1")
# CORRECTED 2026-08-26 (found in the same second-brain vault note as the
# 404 fix above): this read AIRFRAMES_API_KEY, a name that doesn't exist
# anywhere in dispatch-secrets.env (0 bytes -- always empty), while the
# real credential common/acars.py and runner/main.py both actually use is
# AIRFRAMES_TOKEN (68 bytes, present). This watcher has been polling
# airframes.io completely unauthenticated since it was written.
AIRFRAMES_API_KEY     = os.environ.get("AIRFRAMES_TOKEN",         "")
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
# 2026-10-16: the Jumpseat thread's list -- ONLY ever filled from
# GET /api/v1/watchlist/registrations (LADD-filtered by the dispatch API).
# Empty whenever that route was unavailable at the last refresh.
_JUMPSEAT_REGS: set = set()

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
    # ACARSDRAMA_JUMPSEAT_TOKEN is canonical -- namespaced to the site it's for,
    # so it won't collide with an unrelated "jumpseat"-named key down the road.
    # JUMPSEAT_API_KEY kept as a fallback alias for anything still set that way.
    env = (os.environ.get("ACARSDRAMA_JUMPSEAT_TOKEN", "").strip()
           or os.environ.get("JUMPSEAT_API_KEY", "").strip())
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
    """Registration from any supported shape: dumpvdl2 (vdl2.avlc.acars.reg, with its
    leading "." pad stripped), acarsdec / airframes.io / Jumpseat top-level fields,
    or a nested "acars" dict. 2026-10-18 (uplink diagnosis): this read top-level keys
    only, so every local dumpvdl2 datagram on UDP 5005 came back "" and was dropped
    before the watchlist match -- the local path could never fire."""
    return acars_flags.extract_fields(msg)["tail"]


def build_ntfy_payload(reg: str, msg: dict, source: str) -> dict:
    fields = acars_flags.extract_fields(msg)
    tail   = fields["tail"] or reg
    # airframes.io's "flight" enrichment object (a dict, seen live 2026-07-26 on
    # an AA flight) is unwrapped by extract_fields, so its repr() never reaches
    # the ntfy title; dumpvdl2's vdl2.avlc.acars.flight is read the same way.
    flight = fields["flight"]
    label  = (fields["label"]
              or msg.get("type")
              or "ACARS")
    text   = fields["text"]
    freq   = str(msg.get("freq") or msg.get("frequency") or "")
    station = msg.get("station") or {}
    vdl2 = msg.get("vdl2") if isinstance(msg.get("vdl2"), dict) else None
    if vdl2:                                   # dumpvdl2: Hz + a station name string
        try:
            freq = freq or f"{float(vdl2.get('freq')) / 1e6:.3f}"
        except (TypeError, ValueError):
            pass
        station = station or vdl2.get("station") or {}
    sta_loc = (msg.get("stationLocation")
               or (station.get("location") if isinstance(station, dict) else "")
               or "")
    sta_id  = (msg.get("stationIdentifier")
               or (station.get("ident", "") if isinstance(station, dict) else str(station))
               or "")

    signals, priority, edct_raw = classify_text(text)
    sig_tag = f" [{','.join(signals)}]" if signals else ""
    flags = acars_flags.classify(msg)
    if flags and flags != ["free_text"]:
        sig_tag += f" {{{','.join(flags)}}}"

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
        "acars_flags": flags,
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
            # 2026-10-16: count only. The full set includes whatever the
            # LADD filter removes from the Jumpseat list, so naming it here
            # would log a filtered registration by subtraction.
            log.info("Watchlist: %d reg(s)", len(watched))
        else:
            log.warning("Watchlist HTTP %s", resp.status_code)
    except Exception as exc:
        log.error("Watchlist error: %s", exc)
    return watched


def get_jumpseat_registrations() -> Optional[set]:
    """The Jumpseat thread's list: GET /api/v1/watchlist/registrations, the
    flight-watch identifiers with every LADD-listed one removed by the
    dispatch API (2026-10-16). Returns None when the route is unavailable
    (non-200, bad body, network error) -- the caller then polls NOTHING;
    there is no fallback to the full set. Never logs a registration."""
    try:
        resp = DISPATCH_SESSION.get(
            f"{DISPATCH_BASE_URL}/api/v1/watchlist/registrations", timeout=10
        )
        if resp.status_code != 200:
            log.warning("Jumpseat list: HTTP %s from /api/v1/watchlist/registrations "
                        "-- polling nothing until it answers", resp.status_code)
            return None
        data = resp.json()
        regs = data.get("registrations") if isinstance(data, dict) else None
        if not isinstance(regs, list):
            log.warning("Jumpseat list: malformed body -- polling nothing")
            return None
        out = {normalize_reg(r) for r in regs if isinstance(r, str) and r.strip()}
        log.info("Jumpseat list: %d reg(s) (LADD-filtered by the API: %s)",
                 len(out), data.get("ladd_filtered", "?"))
        return out
    except Exception as exc:
        log.warning("Jumpseat list: unavailable (%s) -- polling nothing", exc)
        return None


def _sync_shared_watched(regs: set):
    with _WATCHED_LOCK:
        _WATCHED_REGS.clear()
        _WATCHED_REGS.update(regs)


def _sync_jumpseat_regs(regs: Optional[set]):
    with _WATCHED_LOCK:
        _JUMPSEAT_REGS.clear()
        if regs:
            _JUMPSEAT_REGS.update(regs)


def refresh_watchlists() -> set:
    """Refresh both lists; returns the full set for the main loop's matching."""
    watched = get_watched_registrations()
    _sync_shared_watched(watched)
    _sync_jumpseat_regs(get_jumpseat_registrations())
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
            # 2026-10-18: topic only -- the title carries the registration.
            log.info("ntfy → %s", payload["topic"])
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
                    log.debug("UDP non-JSON datagram line (%d bytes)", len(line))
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

        # CORRECTED 2026-08-26 (an earlier same-day fix here removed the
        # `limit` param based on a confounded manual test -- limit was
        # never the driver; reverted, `limit` is harmless). The real
        # contract, reproduced directly against the live API: it returns
        # **404 "Cannot GET", not 200 with an empty list**, whenever the
        # `since` window contains zero matching messages -- a narrow
        # ~60s window (this poller's own interval) hits that empty case
        # constantly on a low-traffic private feed. Treating 404 here as
        # "no new messages" instead of a failure is the actual fix (this
        # exact root cause was already documented in the second-brain
        # vault from an 08-23 pass and was missed by not searching it
        # first -- see CLAUDE.md's own note on this).
        params = {
            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
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
            elif resp.status_code == 404:
                log.debug("REST airframes: 0 msg(s) since %s (API 404s an "
                          "empty window instead of returning [])",
                          since.isoformat())
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

def _jumpseat_poll_once(seen_ids: set) -> int:
    """One Jumpseat cycle over _JUMPSEAT_REGS (the LADD-filtered list from
    GET /api/v1/watchlist/registrations -- never _WATCHED_REGS). Returns the
    number of registrations queried; 0 when the list is empty, which is
    also what an unavailable route leaves behind."""
    with _WATCHED_LOCK:
        regs = set(_JUMPSEAT_REGS)

    if not regs:
        log.debug("Jumpseat: filtered list empty — nothing polled")
        return 0

    polled = 0
    for reg in regs:
        try:
            resp = JUMPSEAT_SESSION.get(
                f"{JUMPSEAT_API_BASE}/messages/search",
                params={"registration": reg, "limit": "20", "source": "messages"},
                timeout=15,
            )
            polled += 1
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
                log.warning("Jumpseat: HTTP %s", resp.status_code)
        except Exception as exc:
            log.error("Jumpseat poll error: %s", type(exc).__name__)

        # Small inter-registration gap to avoid hammering the API
        time.sleep(2)
    return polled


def jumpseat_poller_thread():
    """
    Polls ACARS Drama Jumpseat /v1/messages/search?registration=<REG>
    every JUMPSEAT_POLL_INT seconds for each registration on the
    LADD-filtered list (GET /api/v1/watchlist/registrations, refreshed by
    main() with the watchlist). 2026-10-16: never the full watched set --
    when the route is unavailable the list is empty and nothing is polled.

    Requires JUMPSEAT_API_KEY env var or ~/.secrets/jumpseat.key.
    Silently idles if no token is available.
    """
    if not _JUMPSEAT_TOKEN:
        log.warning("Jumpseat: no token found — thread idle "
                    "(set JUMPSEAT_API_KEY or write ~/.secrets/jumpseat.key)")
        return

    log.info("Jumpseat poller active — interval %ds (LADD-filtered list only)",
             JUMPSEAT_POLL_INT)
    seen_ids: set = set()

    while True:
        _jumpseat_poll_once(seen_ids)

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

    watched           = refresh_watchlists()
    last_watchlist_ts = time.monotonic()
    seen_ids: set     = set()

    log.info("Main loop running")
    while True:
        if time.monotonic() - last_watchlist_ts >= WATCHLIST_REFRESH_INT:
            watched           = refresh_watchlists()
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

        # >>> gate/baggage hook (2026-10-18 gate batch; owner: acars_gate_forward.py).
        # Every received message, before the registration filter; never raises,
        # never blocks, never changes what this loop does with the message.
        try:
            import acars_gate_forward
            acars_gate_forward.offer(msg)
        except Exception:
            pass
        # <<< gate/baggage hook

        reg_raw = extract_reg_from_msg(msg)
        if not reg_raw:
            continue

        if normalize_reg(reg_raw) in watched:
            handle_watched_match(reg_raw, msg)


_match_counts: dict = {}


def handle_watched_match(reg_raw: str, msg: dict) -> dict:
    """A message from a watched registration: push it (flight-alerts) and count it.
    2026-10-18: logs carry source, signals and type flags only -- never the
    registration or the message text (either can name the aircraft)."""
    source  = msg.get("_source", "UNKNOWN")
    payload = build_ntfy_payload(reg_raw, msg, source)
    signals, _, _ = classify_text(acars_flags.extract_fields(msg)["text"])
    _match_counts[source] = _match_counts.get(source, 0) + 1
    log.info("MATCH [%s] #%d signals=%s flags=%s", source, _match_counts[source],
             signals or "-", payload.get("acars_flags") or "-")
    fire_ntfy({k: v for k, v in payload.items() if k != "acars_flags"})
    return payload


if __name__ == "__main__":
    main()
