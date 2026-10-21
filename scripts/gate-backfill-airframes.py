#!/usr/bin/env python3
"""scripts/gate-backfill-airframes.py -- phase D2: historical arrival gates / baggage
from airframes.io message history (operator 2026-10-20: build; the API's history
reaches back only about 3 days, so every day not run loses data).

Same rules as the live ACARS path: the text is parsed with
common.acars_gate_parser, bound to exactly one SWIM/TFMS-verified dated leg
(gate_assignments.bind_acars), kept only for legs touching DCA/IAD/BWI or on the
watchlist, LADD-checked on the leg's own identifiers (fail closed) and stored
through gate_assignments.ingest_acars. Requests and acks are signals: counted,
never stored. The message text never leaves this process and is never logged.

  * Pull: one pass per phrase (GATE, CAROUSEL, BAG BELT, ARRIVAL GATE) over
    1 h slices (since/until), offset paging 100 per page, >= 2.2 s between calls
    (airframes.io answered x-ratelimit-limit 60/min). A 429 or 401 stops the run
    (exit 4); a 404 is an empty window.
  * The 24 h cut (BACKFILL_MIN_AGE_S, a validation window; operator 2026-10-20),
    for two reasons: (1) no backfill can push on an active watch, and (2) the
    LADD import and LADD sweeps get a full day to run and be confirmed valid
    before any leg is eligible. A message binds to a leg up to 3 h before its
    departure (bind_acars window), so only messages at least
    BACKFILL_MIN_AGE_S + 3 h old are used; newer legs were recorded live by the
    watcher's gate forwarder.
  * Idempotent: the observation key is the parsed fields plus the message's own
    sent time (or a 10-minute bucket), the same as live, so overlaps store once.
  * Token: AIRFRAMES_TOKEN from the environment only (scripts/with-dispatch-env.sh),
    sent as a header, never on a command line, never printed. The call log is
    shapes only: phrase, slice, page, HTTP status, item count.
  * --dry-run fetches nothing: it prints the planned calls and their spacing.
  * History revisions carry reason "backfill:airframes".

Usage:
  scripts/with-dispatch-env.sh python3 scripts/gate-backfill-airframes.py --dry-run --hours 72
  scripts/relay-wait-load.sh && systemd-run --user --unit=corporatetraveldc-gate-backfill-airframes \
    --nice=19 -p CPUQuota=100% -p Slice=production.slice --collect \
    /opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/with-dispatch-env.sh \
    python3 /opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/gate-backfill-airframes.py --hours 72

Exit: 0 done, 2 usage, 4 stopped by the API (429/401).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from common import acars_gate_parser as agp  # noqa: E402
from common import gate_assignments as ga  # noqa: E402

API_BASE = os.environ.get("AIRFRAMES_API_BASE", "https://api.airframes.io/v1")
PHRASES = ("GATE", "CAROUSEL", "BAG BELT", "ARRIVAL GATE")
SLICE_S = 3600
PAGE = 100
SPACING_S = 2.2
BACKFILL_MIN_AGE_S = 86400          # validation window: push safety + LADD sweeps confirmed
BIND_BEFORE_S = 3 * 3600            # bind_acars accepts a message up to 3 h before departure
CUT_S = BACKFILL_MIN_AGE_S + BIND_BEFORE_S
REASON = "backfill:airframes"
UA = "corporatetraveldc-gate-backfill/1.0"


class Stopped(RuntimeError):
    pass


def _iso(ep: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ep))


def _ts(msg: dict) -> float | None:
    return ga._ep(msg.get("timestamp") or msg.get("received_at"))


def text_of(msg: dict) -> str:
    for k in ("cleanedText", "text", "msg_text"):
        if msg.get(k):
            return str(msg[k])
    acars = msg.get("acars") or {}
    return str(acars.get("msg_text") or "") if isinstance(acars, dict) else ""


def envelope(msg: dict) -> dict:
    """The envelope the live forwarder builds (acars_gate_forward._envelope), airframes shape."""
    acars = msg.get("acars") if isinstance(msg.get("acars"), dict) else {}
    flight = msg.get("flightNumber") or msg.get("flight") or acars.get("flight") or ""
    if isinstance(flight, dict):
        flight = flight.get("flightIata") or flight.get("flightIcao") or flight.get("flight") or ""
    direction = msg.get("directionLabel") or msg.get("direction") or msg.get("linkDirection") or ""
    return {"source": "AIRFRAMES", "label": str(msg.get("label") or acars.get("label") or "")[:4],
            "timestamp": msg.get("timestamp") or msg.get("received_at"), "direction": str(direction)[:20],
            "flight": str(flight).strip().upper()[:10], "msg_id": str(msg.get("id") or "")[:40] or None}


def to_item(msg: dict) -> dict | None:
    """Parsed hit + envelope, or None (not an announcement). The text stays here."""
    text = text_of(msg)
    if not agp.is_candidate(text):
        return None
    hits = agp.parse(text)
    if not hits:
        return None
    hit = {k: hits[0][k] for k in ("fmt", "kind", "gate", "terminal", "bag", "flt", "day", "date", "al",
                                    "org", "dst", "station", "sent", "chg") if hits[0].get(k)}
    return {"hit": hit, **envelope(msg)}


def plan(hours: float, now: float) -> list[tuple[str, float, float]]:
    end = now - CUT_S
    start = end - hours * 3600
    out = []
    for phrase in PHRASES:
        t = start
        while t < end:
            out.append((phrase, t, min(t + SLICE_S, end)))
            t += SLICE_S
    return out


def fetch_slice(session, phrase: str, since: float, until: float, *, sleep: Callable = time.sleep,
                log: Callable = print) -> list[dict]:
    msgs, offset = [], 0
    while True:
        sleep(SPACING_S)
        r = session.get(f"{API_BASE}/messages", params={"text": phrase, "since": _iso(since),
                                                        "until": _iso(until), "limit": PAGE,
                                                        "offset": offset}, timeout=20)
        code = getattr(r, "status_code", 0)
        if code in (401, 429):
            log(f"[gate-backfill-airframes] {phrase!r} {_iso(since)} offset {offset}: HTTP {code} -- stopping")
            raise Stopped(str(code))
        if code == 404:
            items = []
        elif code == 200:
            data = r.json()
            items = data if isinstance(data, list) else (data.get("messages") or data.get("data") or [])
        else:
            log(f"[gate-backfill-airframes] {phrase!r} {_iso(since)} offset {offset}: HTTP {code} -- slice skipped")
            return msgs
        log(f"[gate-backfill-airframes] {phrase!r} {_iso(since)} offset {offset}: HTTP {code}, {len(items)} item(s)")
        msgs.extend(m for m in items if isinstance(m, dict))
        if len(items) < PAGE:
            return msgs
        offset += PAGE


def run(*, hours: float, dry_run: bool, now: float | None = None, session=None,
        ingest: Callable | None = None, sleep: Callable = time.sleep, log: Callable = print) -> dict:
    now = time.time() if now is None else now
    calls = plan(hours, now)
    c = {"slices": len(calls), "fetched": 0, "unique": 0, "too_recent": 0, "candidates": 0,
         "received": 0, "stored": 0, "signals": 0, "ladd_dropped": 0}
    if dry_run:
        c["min_seconds"] = round(len(calls) * SPACING_S)
        c["window"] = f"{_iso(now - CUT_S - hours * 3600)}..{_iso(now - CUT_S)}"
        return c
    if session is None:
        import requests
        session = requests.Session()
        session.headers.update({"User-Agent": UA})
        token = os.environ.get("AIRFRAMES_TOKEN", "")
        if token:
            session.headers.update({"Authorization": f"Bearer {token}"})
    ingest = ingest or ga.ingest_acars
    seen: set[str] = set()
    items: list[dict] = []
    for phrase, since, until in calls:
        for m in fetch_slice(session, phrase, since, until, sleep=sleep, log=log):
            c["fetched"] += 1
            mid = str(m.get("id") or "")
            if mid and mid in seen:
                continue
            seen.add(mid) if mid else None
            c["unique"] += 1
            t = _ts(m)
            if t is None or t > now - CUT_S:
                c["too_recent"] += 1
                continue
            item = to_item(m)
            if item:
                c["candidates"] += 1
                items.append(item)
        if len(items) >= 200:
            _merge(c, ingest(items, reason=REASON))
            items = []
    if items:
        _merge(c, ingest(items, reason=REASON))
    return c


def _merge(c: dict, r: dict) -> None:
    for k, v in r.items():
        c[k] = c.get(k, 0) + v


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--hours", type=float, default=72.0, help="history to cover, ending 27 h ago")
    ap.add_argument("--dry-run", action="store_true", help="plan only: no network call, nothing stored")
    a = ap.parse_args(argv)
    if a.hours <= 0:
        return 2
    try:
        counts = run(hours=a.hours, dry_run=a.dry_run)
    except Stopped as e:
        print(f"[gate-backfill-airframes] stopped by the API (HTTP {e}); stored items stay stored", file=sys.stderr)
        return 4
    print(("DRY RUN " if a.dry_run else "") + "[gate-backfill-airframes] "
          + " ".join(f"{k}={v}" for k, v in counts.items()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
