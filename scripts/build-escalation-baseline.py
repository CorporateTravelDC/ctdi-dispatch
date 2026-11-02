#!/usr/bin/env python3
"""scripts/build-escalation-baseline.py -- build time-of-day escalation baselines (2026-11-01).

Reads history from the dispatch database (SELECT only) and writes the baseline file that
src/shared/escalation_baseline.py reads at alert time. Run weekly by
corporatetraveldc-escalation-baseline.timer, or by hand:

    scripts/with-dispatch-env.sh python3 scripts/build-escalation-baseline.py [--out PATH] [--dry-run]

Signals (each measured the same way the live alert measures it):
  tbfm_fix_queue        flights sequenced at a meter fix, sampled every 30 min as the number
                        of rows whose last_seen falls in the preceding 30 min (the live check
                        is db.get_active_tbfm_sequence_count: last_seen within 30 min). The
                        history keeps only each flight's final last_seen, so this approximates
                        the live queue; it is close when the queue dwell is about 30 minutes.
  asdex_movements_hour  STDDS/ASDE-X surface movement events per airport per hour.
  airport_arrivals_hour scheduled arrivals per destination per hour (TFMS milestone records);
                        reference baseline for major airports, not yet wired to an alert.

Bins in which the whole signal recorded nothing (a feed outage) are left out, so an outage
does not drag a baseline toward zero. Prints counts only.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shared.escalation_baseline import BASELINE_FILE, slot_keys  # noqa: E402

MIN_ROWS = {"tbfm_fix_queue": 200, "asdex_movements_hour": 300, "airport_arrivals_hour": 3000}


def parse_time(value) -> float | None:
    """Unix seconds from an ISO-8601 string or an epoch number/string; None if unusable."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile of an ascending list (q in 0..100)."""
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(q / 100 * len(sorted_values)))
    return float(sorted_values[rank - 1])


def bin_counts(times_by_key: dict[str, list[float]], bin_secs: int) -> tuple[dict, set]:
    """{key: {bin_end: count}} with bin_end = the end of the bin holding each time, plus the
    set of bin_ends in which any key recorded something (the signal was alive)."""
    counts: dict[str, dict[int, int]] = {}
    alive: set[int] = set()
    for key, times in times_by_key.items():
        per = counts.setdefault(key, defaultdict(int))
        for t in times:
            end = (int(t) // bin_secs + 1) * bin_secs
            per[end] += 1
            alive.add(end)
    return counts, alive


def series(per_key: dict[int, int], alive: set[int], first: int, last: int,
           bin_secs: int) -> list[tuple[int, int]]:
    """(bin_end, count) for every live bin from first to last, zeros included."""
    return [(b, per_key.get(b, 0)) for b in range(first, last + bin_secs, bin_secs) if b in alive]


def cells_from_samples(samples: list[tuple[int, int]]) -> dict:
    """Group samples into day-of-week and weekday/weekend hour slots with percentiles."""
    grouped: dict[str, list[int]] = defaultdict(list)
    weeks: dict[str, set] = defaultdict(set)
    for when, value in samples:
        iso = datetime.fromtimestamp(when, timezone.utc).isocalendar()
        for slot in slot_keys(when - 1):  # the bin's own hour, not the next one
            grouped[slot].append(value)
            weeks[slot].add((iso[0], iso[1]))
    out = {}
    for slot, values in grouped.items():
        values.sort()
        out[slot] = {"n": len(values), "weeks": len(weeks[slot]),
                     "p50": percentile(values, 50), "p90": percentile(values, 90),
                     "p99": percentile(values, 99),
                     "mean": round(sum(values) / len(values), 2)}
    return out


def build_signal(rows: list[tuple[str, object]], bin_secs: int, min_rows: int) -> dict:
    """{KEY: {slot: cell}} from (key, time) rows."""
    times: dict[str, list[float]] = defaultdict(list)
    for key, raw in rows:
        t = parse_time(raw)
        if key and t:
            times[str(key).upper()].append(t)
    times = {k: v for k, v in times.items() if len(v) >= min_rows}
    if not times:
        return {}
    counts, alive = bin_counts(times, bin_secs)
    last = max(alive)
    out = {}
    for key, per in counts.items():
        first = min(per)
        out[key] = cells_from_samples(series(per, alive, first, last, bin_secs))
    return out


def fetch(sql: str, read_only_role: bool) -> list[tuple]:
    """Run one SELECT on the host over 127.0.0.1 (plain psycopg; the host has no pool).
    Credentials come from the environment loaded by scripts/with-dispatch-env.sh."""
    import psycopg
    env = os.environ
    user = env.get("DISPATCH_PG_RO_USER" if read_only_role else "DISPATCH_PG_USER", "")
    password = env.get("DISPATCH_PG_RO_PASSWORD" if read_only_role else "DISPATCH_PG_PASSWORD", "")
    if not user or not password:
        raise SystemExit("database credentials missing (run through scripts/with-dispatch-env.sh)")
    with psycopg.connect(host="127.0.0.1", port=env.get("DISPATCH_PG_PORT", "5432"),
                         dbname=env.get("DISPATCH_PG_DB", "corporatetraveldc"), user=user,
                         password=password, connect_timeout=10,
                         options="-c default_transaction_read_only=on") as c:
        return [tuple(r) for r in c.execute(sql).fetchall()]


QUERIES = {
    "tbfm_fix_queue": ("SELECT meter_fix, last_seen FROM tbfm_sequences", 1800),
    "asdex_movements_hour": ("SELECT airport, event_time FROM surface_movement_events", 3600),
    "airport_arrivals_hour": ("SELECT destination, original_arrival FROM flight_ooooi_times "
                              "WHERE original_arrival IS NOT NULL AND original_arrival <> ''", 3600),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=BASELINE_FILE)
    ap.add_argument("--dry-run", action="store_true", help="build and report, write nothing")
    ap.add_argument("--read-only-role", action="store_true",
                    help="connect as DISPATCH_PG_RO_USER instead of DISPATCH_PG_USER")
    args = ap.parse_args(argv)
    signals, report = {}, {}
    for name, (sql, bin_secs) in QUERIES.items():
        rows = fetch(sql, args.read_only_role)
        built = build_signal(rows, bin_secs, MIN_ROWS[name])
        signals[name] = built
        report[name] = {"rows": len(rows), "keys": len(built),
                        "cells": sum(len(v) for v in built.values())}
        print(f"[baseline] {name}: {len(rows)} rows -> {len(built)} keys, "
              f"{report[name]['cells']} cells", flush=True)
    doc = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "local_tz": "America/New_York", "report": report, "signals": signals}
    if args.dry_run:
        print("[baseline] dry run: nothing written")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")))
    os.replace(tmp, args.out)
    print(f"[baseline] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
