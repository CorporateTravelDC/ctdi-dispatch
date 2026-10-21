#!/usr/bin/env python3
"""scripts/gate-backfill-tdes.py -- phase D1: historical departure gates from SWIM TDES.

Replays the stored TDES clearances (tdes_departure_events, append-only since
2026-08-30) through the SAME binder the live path uses (common.gate_assignments
.record_tdes), so the history obeys every phase B rule: source precedence,
placeholders never count, LADD-listed legs dropped before storage, no FIDS time,
status or tail. Operator decisions 2026-10-20: build and run D1; keep the 24 h cut.

  * Keyset-paged over (event_time, airport, callsign), never OFFSET; each row is
    visited exactly once.
  * The 24 h cut (BACKFILL_MIN_AGE_S, a validation window; operator 2026-10-20):
    no leg whose scheduled departure is within the last 24 h is touched, for two
    reasons -- (1) a backfill can never make the poller push on an active watch,
    and (2) the LADD import and the LADD sweeps (leak scans, tripwire, demo scrub
    gate) get a full day to run and be confirmed valid before any leg becomes
    eligible, so a leg is only backfilled against a LADD list that has already
    been checked. A clearance binds to a leg scheduled up to 6 h after it
    (tdes_leg's [-2 h, +6 h] window), so the row cut is event_time <= now -
    (BACKFILL_MIN_AGE_S + 6 h). Recent legs were recorded live anyway:
    record_tdes has run in the STDDS ingest since 2026-10-18 23:17Z.
  * LADD: each page's callsigns go through db.faa_ladd_listed_batch BEFORE any
    store; listed rows are skipped (counted). A lookup failure stops the run
    with exit 5 and nothing more is stored (same as the demo scrub). record()
    checks LADD again per leg, fail closed.
  * Idempotent: the observation content key is built from the TDES row's own
    primary key (airport|callsign|event_time), the same event key the live path
    uses, so a rerun or an overlap with live writes stores nothing twice.
  * No push path is ever called: record() only stores and re-resolves; history
    revisions carry reason "backfill:tdes".
  * Load: waits before every page while load1 >= GATE_BACKFILL_MAX_LOAD
    (default 24; the thermal lockdown is 40), like scripts/scrub-demo-source.py.
  * Output: counts only -- never a callsign, gate or leg.

Usage (with the dispatch env):
  scripts/with-dispatch-env.sh python3 scripts/gate-backfill-tdes.py --dry-run
  scripts/with-dispatch-env.sh python3 scripts/gate-backfill-tdes.py --g-prefix-report
  scripts/with-dispatch-env.sh python3 scripts/gate-backfill-tdes.py            # real run

Real run as its own transient user unit (not inside a relay):
  scripts/relay-wait-load.sh && systemd-run --user --unit=corporatetraveldc-gate-backfill-tdes \
    --nice=19 -p CPUQuota=100% -p Slice=production.slice --collect \
    /opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/with-dispatch-env.sh \
    python3 /opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/gate-backfill-tdes.py

Exit: 0 done, 2 usage, 5 LADD lookup failed (run stopped).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common import db  # noqa: E402
from common import gate_assignments as ga  # noqa: E402

BACKFILL_MIN_AGE_S = 86400          # validation window: push safety + LADD sweeps confirmed
BIND_AHEAD_S = 6 * 3600             # tdes_leg binds a leg up to 6 h after the clearance
CUT_S = BACKFILL_MIN_AGE_S + BIND_AHEAD_S
PAGE = int(os.environ.get("GATE_BACKFILL_PAGE", "2000"))
MAX_LOAD = float(os.environ.get("GATE_BACKFILL_MAX_LOAD", "24"))
LOAD_POLL_S = float(os.environ.get("GATE_BACKFILL_LOAD_POLL_S", "15"))
REASON = "backfill:tdes"
COLS = ("airport, callsign, event_time, parking_gate, destination_airport, clearance_delivery_time, "
        "sfdps_gufi, last_seen")


class LaddUnavailable(RuntimeError):
    pass


def iso(ep: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ep))


def wait_for_headroom(sleep: Callable[[float], None] = time.sleep,
                      loadavg: Callable[[], tuple] = os.getloadavg) -> float:
    waited, said = 0.0, False
    while (l1 := loadavg()[0]) >= MAX_LOAD:
        if not said:
            print(f"[gate-backfill-tdes] load1 {l1:.1f} >= {MAX_LOAD:g} -- pausing", flush=True)
            said = True
        sleep(LOAD_POLL_S)
        waited += LOAD_POLL_S
    return waited


def pages(cut_iso: str, page: int = PAGE, limit: int | None = None):
    """Keyset pages of rows with event_time <= cut_iso, ascending. Yields lists of dicts."""
    last = None
    seen = 0
    while True:
        n = page if limit is None else min(page, limit - seen)
        if n <= 0:
            return
        with db.conn() as c:
            if last is None:
                rows = c.execute(f"SELECT {COLS} FROM tdes_departure_events WHERE event_time <= ? "
                                 "ORDER BY event_time, airport, callsign LIMIT ?", (cut_iso, n)).fetchall()
            else:
                rows = c.execute(f"SELECT {COLS} FROM tdes_departure_events WHERE event_time <= ? "
                                 "AND (event_time > ? OR (event_time = ? AND (airport > ? OR "
                                 "(airport = ? AND callsign > ?)))) "
                                 "ORDER BY event_time, airport, callsign LIMIT ?",
                                 (cut_iso, last[0], last[0], last[1], last[1], last[2], n)).fetchall()
        rows = [dict(r) for r in rows]
        if not rows:
            return
        seen += len(rows)
        last = (rows[-1]["event_time"], rows[-1]["airport"], rows[-1]["callsign"])
        yield rows


def ladd_page(rows: list[dict]) -> set[str]:
    try:
        return {s.upper() for s in db.faa_ladd_listed_batch([r["callsign"] for r in rows if r.get("callsign")])}
    except Exception as e:
        raise LaddUnavailable(type(e).__name__) from e


def _content_key(rec: dict) -> tuple[str | None, bool]:
    """(content_key or None, placeholder) the live binder would write -- read-only."""
    key, match = ga.tdes_leg(rec.get("callsign"), rec.get("airport"), rec.get("destination_airport"),
                             rec.get("event_time"))
    if key is None:
        return None, False
    norm, placeholder, _ = ga.normalize_value("gate", rec.get("parking_gate"), "swim_tdes")
    ev = f"{rec.get('airport')}|{rec.get('callsign')}|{rec.get('event_time')}"
    obs = ga.Observation(key=key, side="departure", field="gate", value_raw=rec.get("parking_gate"),
                         source="swim_tdes", observed_at=ga.iso_utc(rec.get("event_time")) or "",
                         leg_match=match, evidence_type="clearance", confidence=0.95,
                         parser_version="tdes-1", airport=rec.get("airport"),
                         raw_table="tdes_departure_events", raw_ref=ev, event_key=ev)
    return obs.content_key(norm), placeholder


def run(*, dry_run: bool, limit: int | None = None, now: float | None = None,
        record_fn: Callable | None = None, headroom: Callable | None = None) -> dict:
    now = time.time() if now is None else now
    record_fn = record_fn or ga.record_tdes
    headroom = headroom or wait_for_headroom
    cut = iso(now - CUT_S)
    c = {"pages": 0, "rows": 0, "no_gate": 0, "skipped_ladd": 0, "placeholders": 0, "unbound": 0,
         "would_store": 0, "already_stored": 0, "stored": 0, "duplicates": 0, "ladd_dropped": 0,
         "store_errors": 0}
    with db.conn() as conn:
        ga.ensure_tables(conn)
        c["skipped_recent"] = conn.execute("SELECT count(*) AS n FROM tdes_departure_events WHERE event_time > ?",
                                           (cut,)).fetchone()["n"]
    for rows in pages(cut, limit=limit):
        headroom()
        c["pages"] += 1
        c["rows"] += len(rows)
        listed = ladd_page(rows)
        for rec in rows:
            if rec.get("parking_gate") is None:
                c["no_gate"] += 1
                continue
            if (rec.get("callsign") or "").upper() in listed:
                c["skipped_ladd"] += 1
                continue
            if dry_run:
                ck, placeholder = _content_key(rec)
                if ck is None:
                    c["unbound"] += 1
                    continue
                c["placeholders"] += placeholder
                with db.conn() as conn:
                    hit = conn.execute("SELECT 1 FROM gate_baggage_observations WHERE content_key = ?",
                                       (ck,)).fetchone()
                c["already_stored" if hit else "would_store"] += 1
                continue
            _n, ph, _f = ga.normalize_value("gate", rec.get("parking_gate"), "swim_tdes")
            c["placeholders"] += ph
            try:
                key, res = record_fn(rec, reason=REASON)
            except Exception as e:
                c["store_errors"] += 1
                print(f"[gate-backfill-tdes] store error ({type(e).__name__})", file=sys.stderr)
                continue
            if key is None or res is None:
                c["unbound"] += 1
                continue
            c["stored"] += res.stored
            c["duplicates"] += res.duplicates
            c["ladd_dropped"] += int(res.ladd_dropped)
        print(f"[gate-backfill-tdes] page {c['pages']}: rows {c['rows']}", flush=True)
    return c


def g_prefix_report() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    with db.conn() as c:
        rows = c.execute("SELECT airport, parking_gate FROM tdes_departure_events "
                         "WHERE parking_gate IS NOT NULL").fetchall()
    for r in rows:
        ap = r["airport"] or "?"
        g = str(r["parking_gate"]).strip().upper()
        d = out.setdefault(ap, {"g_prefixed": 0, "plain": 0, "placeholder": 0})
        if g in ga._PLACEHOLDERS:
            d["placeholder"] += 1
        elif ga._G_PREFIX_RE.match(g):
            d["g_prefixed"] += 1
        else:
            d["plain"] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dry-run", action="store_true", help="count only; store nothing")
    ap.add_argument("--limit", type=int, help="stop after this many TDES rows (testing)")
    ap.add_argument("--g-prefix-report", action="store_true", help="per-airport counts of G-prefixed gates")
    a = ap.parse_args(argv)
    if a.g_prefix_report:
        for airport, d in sorted(g_prefix_report().items()):
            print(f"{airport}\tg_prefixed={d['g_prefixed']}\tplain={d['plain']}\tplaceholder={d['placeholder']}")
        return 0
    try:
        counts = run(dry_run=a.dry_run, limit=a.limit)
    except LaddUnavailable as e:
        print(f"[gate-backfill-tdes] LADD lookup failed ({e}) -- run stopped, nothing more stored", file=sys.stderr)
        return 5
    print(("DRY RUN " if a.dry_run else "") + "[gate-backfill-tdes] "
          + " ".join(f"{k}={v}" for k, v in counts.items()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
