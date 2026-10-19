"""
acars_gate_forward.py -- gate / baggage announcement forwarder for the ACARS watcher
(2026-10-18, gate batch; owner of this file: the gate/baggage work, not the
watcher's message classification).

acars_watcher.py calls offer(msg) once per received message (airframes.io,
Jumpseat, local UDP) from ONE marked call site in its main loop. offer() never
raises, never blocks and never changes what the watcher does with the message.

It parses the text with acars_gate_parser (a byte-identical copy of
src/common/acars_gate_parser.py -- tests/common/test_acars_gate_parser.py pins
the copy) and keeps only gate / baggage ANNOUNCEMENTS (requests and the PSA ack
are signals only and are dropped here). What leaves this container is the
parsed fields plus a small envelope -- source, label, time, direction, flight
id, message id. Never the message text (SkyWest uplinks carry crew names after
the gate), never a registration or hex: the dispatch side binds the hit to a
SWIM-verified leg and runs the LADD check on that leg's own identifiers.

Batches go to POST {DISPATCH_BASE_URL}/api/v1/gate-observations/acars (admin
token, audited) every GATE_FORWARD_INTERVAL seconds (default 300) or at 50
hits, from a daemon thread. A full queue drops the newest hit (counted).
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time

try:
    import acars_gate_parser as agp
except Exception:                       # pragma: no cover - image without the parser
    agp = None

log = logging.getLogger("acars_gate_forward")

DISPATCH_BASE_URL = os.environ.get("DISPATCH_BASE_URL", "http://100.x.x.x:8000")
INTERVAL_S = int(os.environ.get("GATE_FORWARD_INTERVAL", "300") or 300)
BATCH_MAX = 50
ENABLED = os.environ.get("GATE_FORWARD_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")

_Q: "queue.Queue[dict]" = queue.Queue(maxsize=2000)
_started = False
_lock = threading.Lock()
_stats = {"offered": 0, "queued": 0, "dropped_full": 0, "posted": 0, "post_errors": 0}

_HIT_KEYS = ("fmt", "kind", "gate", "terminal", "bag", "flt", "day", "date", "al", "org", "dst",
             "station", "sent", "chg")


def _text_of(msg: dict) -> str:
    for k in ("cleanedText", "text", "msg_text"):
        v = msg.get(k)
        if v:
            return str(v)
    acars = msg.get("acars") or ((msg.get("vdl2") or {}).get("avlc") or {}).get("acars") or {}
    return str(acars.get("msg_text") or "") if isinstance(acars, dict) else ""


def _envelope(msg: dict) -> dict:
    avlc = (msg.get("vdl2") or {}).get("avlc") or {}
    acars = msg.get("acars") or avlc.get("acars") or {}
    if not isinstance(acars, dict):
        acars = {}
    flight = msg.get("flightNumber") or msg.get("flight") or acars.get("flight") or ""
    if isinstance(flight, dict):               # airframes.io flight object
        flight = flight.get("flightIata") or flight.get("flightIcao") or flight.get("flight") or ""
    direction = (msg.get("directionLabel") or msg.get("direction") or msg.get("linkDirection") or "")
    if not direction:
        src_type = ((avlc.get("src") or {}).get("type") or "").lower()
        direction = "down" if src_type == "aircraft" else ("up" if "ground" in src_type else "")
    ts = msg.get("timestamp") or msg.get("received_at")
    if not ts:
        t = (msg.get("vdl2") or {}).get("t")
        ts = t.get("sec") if isinstance(t, dict) else None
    return {
        "source": (msg.get("_source") or "").upper(),
        "label": str(msg.get("label") or acars.get("label") or "")[:4],
        "timestamp": ts if ts not in (None, "") else time.time(),
        "direction": str(direction)[:20],
        "flight": str(flight).strip().upper()[:10],
        "msg_id": str(msg.get("id") or "")[:40] or None,
    }


def _ensure_thread() -> None:
    global _started
    with _lock:
        if _started:
            return
        threading.Thread(target=_flusher, daemon=True, name="gate-forward").start()
        _started = True


def offer(msg: dict) -> None:
    """Called once per received message. Never raises."""
    if not ENABLED or agp is None:
        return
    try:
        _stats["offered"] += 1
        text = _text_of(msg)
        if not agp.is_candidate(text):
            return
        hits = agp.parse(text)
        if not hits or hits[0].get("kind") not in agp.ASSIGNMENT_KINDS:
            return
        hit = {k: hits[0][k] for k in _HIT_KEYS if hits[0].get(k)}
        item = {"hit": hit, **_envelope(msg)}
        if item["source"] not in ("AIRFRAMES", "JUMPSEAT", "LOCAL"):
            return
        _ensure_thread()
        try:
            _Q.put_nowait(item)
            _stats["queued"] += 1
        except queue.Full:
            _stats["dropped_full"] += 1
    except Exception as exc:                 # never let the watcher's loop see an error
        log.debug("gate forward: offer failed: %s", type(exc).__name__)


def _post(batch: list[dict], session=None) -> None:
    import requests
    s = session or requests
    token = os.environ.get("DISPATCH_ADMIN_TOKEN", "")
    try:
        r = s.post(f"{DISPATCH_BASE_URL}/api/v1/gate-observations/acars",
                   json={"items": batch},
                   headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                   timeout=20)
        if getattr(r, "status_code", 0) in (200, 201):
            _stats["posted"] += len(batch)
        else:
            _stats["post_errors"] += 1
            log.warning("gate forward: HTTP %s for a batch of %d", getattr(r, "status_code", "?"), len(batch))
    except Exception as exc:
        _stats["post_errors"] += 1
        log.warning("gate forward: post failed (%s)", type(exc).__name__)


def drain(max_items: int = BATCH_MAX) -> list[dict]:
    out: list[dict] = []
    while len(out) < max_items:
        try:
            out.append(_Q.get_nowait())
        except queue.Empty:
            break
    return out


def _flusher() -> None:
    last = time.monotonic()
    while True:
        time.sleep(5)
        if _Q.qsize() >= BATCH_MAX or (time.monotonic() - last >= INTERVAL_S and not _Q.empty()):
            batch = drain()
            if batch:
                _post(batch)
                log.info("gate forward: posted %d hit(s) (totals %s)", len(batch), _stats)
            last = time.monotonic()
