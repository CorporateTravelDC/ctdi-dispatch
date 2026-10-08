#!/usr/bin/env python3
"""scripts/research/airline_time_fidelity_rollup.py -- rolling 30/60/90-day
summaries of the airline time-fidelity research monitor (2026-10-08,
docs/research/AIRLINE_TIME_FIDELITY_SPEC.md). Host-side, stdlib only, reads
the collector's JSONL; never touches the database.

The monitor never stops: raw snapshots are kept (they are the only lasting
copy of the FAA surface events, which the source table rolls off), and every
run rebuilds the rolling windows, the same way the 30-day facility disruption
baseline is rebuilt.

Writes, under <out>/rolling/:
  private-<N>d.json  full breakdown: operating carrier, marketing brand
                     (codeshare), route, route x carrier, weekday, hour. PRIVATE
                     LEDGER: never published.
  public-anchors-<N>d.json  anchor carriers only (operating = brand, mainline),
                     per anchor and per DC airport; no codeshare, no route x
                     operator, no flight numbers (operator, 2026-10-08).
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WINDOWS = (30, 60, 90)
ET = ZoneInfo("America/New_York")
DC = ("KDCA", "KIAD", "KBWI")
# Public anchors (operator, 2026-10-08): the US big three plus major European and Gulf carriers.
ANCHORS = ("AAL", "DAL", "UAL", "BAW", "DLH", "AFR", "KLM", "VIR", "UAE", "ETD", "QTR", "SWR", "AUA")


def ts(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None
    except ValueError:
        return None


def load(out_dir: Path, now: datetime, days: int) -> list[dict]:
    """Latest snapshot per flight (gufi) whose filed departure falls in the window."""
    start = now - timedelta(days=days)
    best: dict[str, dict] = {}
    for f in sorted(out_dir.glob("*.jsonl")):
        try:
            day = datetime.strptime(f.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if day < start - timedelta(days=1):
            continue
        for ln in f.open():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("kind") != "flight":
                continue
            fd = ts((r.get("reported") or {}).get("original_departure"))
            if fd is None or not (start <= fd <= now):
                continue
            k = r.get("gufi") or f"{r.get('callsign')}|{fd.isoformat()}"
            if k not in best or r["collected_at"] > best[k]["collected_at"]:
                best[k] = r
    return list(best.values())


def metrics(rows: list[dict]) -> dict:
    def vals(key):
        return [r["derived"][key] for r in rows if r["derived"].get(key) is not None]
    arr = vals("on_vs_filed_min")
    offs = vals("off_vs_surface_min")
    out = {
        "flights": len(rows),
        "arrivals_reported": len(arr),
        "late_over_15_pct": round(100 * sum(1 for d in arr if d > 15) / len(arr), 1) if arr else None,
        "median_on_vs_filed_min": round(statistics.median(arr), 1) if arr else None,
        "off_frozen_pct": round(100 * sum(1 for r in rows if r["derived"].get("frozen_off")) / len(rows), 1) if rows else None,
        "off_vs_surface_pairs": len(offs),
        "off_within_1_5_min_pct": round(100 * sum(1 for d in offs if abs(d) <= 1.5) / len(offs), 1) if offs else None,
        "median_off_vs_surface_min": round(statistics.median(offs), 1) if offs else None,
    }
    for k in ("reported_taxi_out_min", "observed_ramp_exit_to_off_min", "on_vs_surface_min"):
        v = vals(k)
        out[f"median_{k}"] = round(statistics.median(v), 1) if v else None
    return out


def group(rows, keyfn) -> dict:
    g = defaultdict(list)
    for r in rows:
        for k in keyfn(r):
            if k:
                g[k].append(r)
    return {k: metrics(v) for k, v in sorted(g.items())}


def _dep_et(r):
    t = ts((r.get("reported") or {}).get("original_departure"))
    return t.astimezone(ET) if t else None


def private_summary(rows: list[dict]) -> dict:
    return {
        "all": metrics(rows),
        "by_operating_carrier": group(rows, lambda r: [r["airline"]]),
        "by_brand": group(rows, lambda r: r.get("brands") or [r["airline"]]),
        "by_route": group(rows, lambda r: [f"{r['origin']}>{r['destination']}"]),
        "by_route_carrier": group(rows, lambda r: [f"{r['origin']}>{r['destination']}|{r['airline']}"]),
        "by_weekday_et": group(rows, lambda r: [_dep_et(r).strftime("%a")] if _dep_et(r) else []),
        "by_hour_et_3h": group(rows, lambda r: [f"{_dep_et(r).hour // 3 * 3:02d}"] if _dep_et(r) else []),
        "by_scope": group(rows, lambda r: r.get("scope") or []),
    }


def public_summary(rows: list[dict]) -> dict:
    """Anchors only, mainline-operated (operating carrier IS the anchor). No brands,
    no flight numbers, no route x operator."""
    anchored = [r for r in rows if r["airline"] in ANCHORS]
    return {
        "anchors": list(ANCHORS),
        "by_anchor": group(anchored, lambda r: [r["airline"]]),
        "by_anchor_dc_airport": group(anchored, lambda r: [f"{r['airline']}|{a}" for a in (r["origin"], r["destination"])
                                                         if a in DC]),
    }


def run(out_dir: Path, now: datetime | None = None) -> list[Path]:
    now = now or datetime.now(timezone.utc)
    roll = out_dir / "rolling"
    roll.mkdir(parents=True, exist_ok=True)
    written = []
    for days in WINDOWS:
        rows = load(out_dir, now, days)
        span = sorted(_dep_et(r) for r in rows if _dep_et(r))
        meta = {"window_days": days, "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "flights": len(rows),
                "data_from": span[0].isoformat() if span else None, "data_to": span[-1].isoformat() if span else None,
                "note": "window is partial until the monitor has run for the full period"}
        for name, body in ((f"private-{days}d.json", private_summary(rows)),
                           (f"public-anchors-{days}d.json", public_summary(rows))):
            p = roll / name
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"meta": meta, **body}, indent=1, sort_keys=True))
            tmp.replace(p)
            written.append(p)
    return written


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/corporatetraveldc/research/airline-time-fidelity")
    for p in run(out):
        print(f"rollup: {p}")
