#!/usr/bin/env python3
"""scripts/quiet-window-report.py -- rolling 30-day load profile to pick
randomized maintenance windows (operator directive 2026-10-04: "monitor 30
days rolling ... of slow period for randomized maintenance candidate windows
for this and other tripwires").

Source: /var/lib/corporatetraveldc/ollama-keepwarm/thermal-samples.csv, the
5-minute thermal-sample rows (load1/load5 columns present since 2026-09-21).
Buckets samples by local (America/New_York) weekday x hour, reports p50/p90
per bucket, and lists the quietest contiguous windows per weekday and overall.

Outputs (never a tracked file -- the report changes daily and would drift the
signed manifest):
  /var/lib/corporatetraveldc/reports/quiet-windows.md     (rewritten each run)
  /var/lib/corporatetraveldc/reports/quiet-windows.json   (machine-readable:
      per-bucket stats + ranked windows, for tripwires that want to draw a
      slot from the quiet set instead of a fixed clock)
  --vault  also writes the markdown as a second-brain note (weekly timer).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SRC = "/var/lib/corporatetraveldc/ollama-keepwarm/thermal-samples.csv"
OUT_DIR = "/var/lib/corporatetraveldc/reports"
LOCAL = ZoneInfo("America/New_York")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    k = (len(vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return round(vals[f] + (vals[c] - vals[f]) * (k - f), 2)


def load_samples(path, days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    buckets = defaultdict(list)  # (weekday, hour) -> [load1]
    n = 0
    first = last = None
    with open(path, newline="") as fh:
        for row in csv.reader(fh):
            if len(row) < 8 or row[0] == "timestamp":
                continue
            try:
                # SUPERSEDED 2026-10-06: "sampler stamps local wall-clock time (America/New_York)"
                # bucket on the Eastern wall-clock whatever the stamp's zone
                ts = parse_sample_ts(row[0]).astimezone(LOCAL)
                load1 = float(row[6])
            except (ValueError, IndexError):
                continue
            if ts < cutoff:
                continue
            buckets[(ts.weekday(), ts.hour)].append(load1)
            n += 1
            first = first or ts
            last = ts
    return buckets, n, first, last


# 2026-10-06: the host clock moved from America/New_York to UTC at this
# instant. Naive rows stamped before it are Eastern wall-clock; naive rows
# after it are UTC wall-clock. The two never overlap: the last Eastern row is
# 2026-10-06 13:15 (=17:15Z) and the first UTC row is 2026-10-06 17:20.
# New rows carry an explicit Z (scripts/thermal-sample.sh).
HOST_UTC_SINCE = datetime(2026, 10, 6, 17, 18, 25, tzinfo=timezone.utc)
_EASTERN_NAIVE_BEFORE = HOST_UTC_SINCE.astimezone(LOCAL).replace(tzinfo=None)


def parse_sample_ts(raw):
    """Timestamp of one thermal sample as an aware datetime (see HOST_UTC_SINCE)."""
    if raw.endswith("Z"):
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    if naive < _EASTERN_NAIVE_BEFORE:
        return naive.replace(tzinfo=LOCAL)
    return naive.replace(tzinfo=timezone.utc)


def rank_windows(buckets, width_h, top):
    """Rank every contiguous window of width_h hours (wrapping midnight,
    within a weekday-local day sequence) by the mean of bucket p90s."""
    out = []
    for wd in range(7):
        for start in range(24):
            hours = [(wd + (start + i) // 24) % 7 for i in range(width_h)], [(start + i) % 24 for i in range(width_h)]
            p90s = []
            for d, h in zip(*hours):
                v = buckets.get((d, h))
                if not v:
                    p90s = None
                    break
                p90s.append(pct(v, 0.9))
            if p90s is None:
                continue
            out.append({"weekday": DAYS[wd], "start_hour": start, "width_h": width_h,
                        "p90_mean": round(statistics.mean(p90s), 2), "p90_max": max(p90s)})
    out.sort(key=lambda w: (w["p90_mean"], w["p90_max"]))
    return out[:top]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--width", type=int, default=2, help="window width in hours")
    ap.add_argument("--vault", action="store_true", help="also write the markdown as a second-brain note")
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--out-dir", default=OUT_DIR)
    a = ap.parse_args()

    buckets, n, first, last = load_samples(a.src, a.days)
    if n == 0:
        print("no samples with load columns in window", file=sys.stderr)
        return 1
    span_days = (last - first).total_seconds() / 86400 if first and last else 0

    stats = {f"{DAYS[d]}-{h:02d}": {"n": len(v), "p50": pct(v, 0.5), "p90": pct(v, 0.9), "max": round(max(v), 2)}
             for (d, h), v in sorted(buckets.items())}
    overall = rank_windows(buckets, a.width, 12)
    ranked_all = rank_windows(buckets, a.width, 500)
    per_day = {day: [w for w in ranked_all if w["weekday"] == day][:3] for day in DAYS}
    # Rolling maintenance windows (operator directive 2026-10-04): per weekday
    # choose N_WIN windows of a.width hours, >= SPACING_H apart so they are
    # spread through the day, drawn at random from that day's top-ranked set
    # (not always the same top-N, so the pattern moves day to day). Always
    # guarantee one overnight window (start 22:00-03:00) so the historic
    # 23:00-05:00 floor is never lost. Re-drawn on every daily run.
    N_WIN, SPACING_H, POOL = 4, 4, 10
    maint = {}
    for day in DAYS:
        pool = [w for w in ranked_all if w["weekday"] == day][:POOL]
        random.shuffle(pool)
        chosen = []
        for w in sorted(pool, key=lambda w: w["p90_mean"] + random.uniform(0, 3)):
            if all(min(abs(w["start_hour"] - c["start_hour"]), 24 - abs(w["start_hour"] - c["start_hour"])) >= SPACING_H for c in chosen):
                chosen.append(w)
            if len(chosen) == N_WIN:
                break
        if not any(c["start_hour"] >= 22 or c["start_hour"] <= 3 for c in chosen):
            night = [w for w in ranked_all if w["weekday"] == day and (w["start_hour"] >= 22 or w["start_hour"] <= 3)]
            if night:
                chosen.append(night[0])
        chosen.sort(key=lambda w: w["start_hour"])
        maint[day] = [{"start_hour": c["start_hour"], "width_h": a.width, "p90_mean": c["p90_mean"]} for c in chosen]
    today_day = datetime.now(LOCAL).strftime("%a")
    all_loads = [x for v in buckets.values() for x in v]

    md = []
    md.append(f"# Quiet windows -- rolling {a.days}-day load profile (generated {datetime.now(LOCAL):%Y-%m-%d %H:%M %Z})")
    md.append("")
    md.append(f"Samples: {n} (5-min) spanning {span_days:.1f} days ({first:%Y-%m-%d} -> {last:%Y-%m-%d}); "
              f"overall load1 p50 {pct(all_loads,0.5)} / p90 {pct(all_loads,0.9)} / max {max(all_loads):.1f}. "
              f"Window width {a.width}h; ranked by mean of hourly p90 (lower = quieter). "
              f"{'NOTE: fewer than 30 days of data yet -- profile still filling in.' if span_days < 29 else ''}")
    md.append("")
    md.append(f"## Quietest {a.width}h windows, any day")
    md.append("")
    md.append("| rank | day | start | p90 mean | p90 max |")
    md.append("|---|---|---|---|---|")
    for i, w in enumerate(overall, 1):
        md.append(f"| {i} | {w['weekday']} | {w['start_hour']:02d}:00 | {w['p90_mean']} | {w['p90_max']} |")
    md.append("")
    md.append(f"## Quietest {a.width}h windows per weekday (top 3)")
    md.append("")
    md.append("| day | 1st | 2nd | 3rd |")
    md.append("|---|---|---|---|")
    for day in DAYS:
        cells = [f"{w['start_hour']:02d}:00 (p90 {w['p90_mean']})" for w in per_day[day]] or ["--"]
        md.append(f"| {day} | " + " | ".join(cells + ["--"] * (3 - len(cells))) + " |")
    md.append("")
    md.append("## Rolling maintenance windows (drawn today; the guard reads these in --rolling mode)")
    md.append("")
    md.append("| day | windows (start, " + f"{a.width}h) |")
    md.append("|---|---|")
    for day in DAYS:
        md.append(f"| {day}{' (today)' if day == today_day else ''} | " + ", ".join(f"{w['start_hour']:02d}:00 (p90 {w['p90_mean']})" for w in maint[day]) + " |")
    md.append("")
    md.append("## Weekday x hour p90 heatmap (load1)")
    md.append("")
    md.append("| day | " + " | ".join(f"{h:02d}" for h in range(24)) + " |")
    md.append("|---|" + "---|" * 24)
    for d, day in enumerate(DAYS):
        cells = []
        for h in range(24):
            v = buckets.get((d, h))
            cells.append(f"{pct(v,0.9):.0f}" if v else "·")
        md.append(f"| {day} | " + " | ".join(cells) + " |")
    md.append("")
    md.append("Current fixed slots for reference: weekly stack refresh Sun 04:15; nightly tripwire window 23:30-03:30; "
              "second-brain-daily 23:45; long-runner cluster 20:20-01:00 (see the lock schedule table in the 2026-10-04 notes). "
              "Candidate re-spacing = move long-runners into the top-ranked windows above.")
    text = "\n".join(md) + "\n"

    os.makedirs(a.out_dir, exist_ok=True)
    with open(os.path.join(a.out_dir, "quiet-windows.md"), "w") as fh:
        fh.write(text)
    with open(os.path.join(a.out_dir, "quiet-windows.json"), "w") as fh:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(), "days": a.days, "samples": n,
                   "span_days": round(span_days, 1), "width_h": a.width, "buckets": stats,
                   "quietest_overall": overall, "quietest_per_day": per_day,
                   "maintenance_candidates": maint, "today": today_day,
                   "today_windows": maint[today_day]}, fh, indent=1)
    print(f"quiet-window report: {n} samples / {span_days:.1f} days -> {a.out_dir}/quiet-windows.{{md,json}}")
    for w in overall[:3]:
        print(f"  quietest: {w['weekday']} {w['start_hour']:02d}:00 +{a.width}h  p90 mean {w['p90_mean']}")

    if a.vault:
        # second_brain.remember expects env via scripts/with-dispatch-env.sh (the
        # timer unit wraps this script in it); never shell-source the env files.
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
        try:
            from second_brain.remember import remember_text  # type: ignore
            path = remember_text(text, tags="ops,load-profile,quiet-windows,maintenance-candidates,agent-authored",
                                 author_kind="agent")
            print(f"  vault: {path}")
        except Exception as e:  # report, never fail the local report over the vault
            print(f"  vault write skipped: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
