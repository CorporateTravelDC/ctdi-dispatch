#!/usr/bin/env python3
"""scripts/backup/direct_retention.py -- which direct-backup run folders to delete (2026-11-19).

  direct_retention.py [--daily 7] [--weekly 4] [--monthly 3] < run-folder names (one per line)

Prints the folders to DELETE, one per line. Only names this script's backups create are ever considered
(UTC run stamps, e.g. 20261119T034000Z); anything else on the remote is ignored and never printed, so the
caller can only ever delete its own runs. Keeps the newest run of each of the last N days, ISO weeks and
months (a run may count for more than one rule). The newest run is always kept.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone

RUN = re.compile(r"^(\d{8}T\d{6}Z)/?$")


def plan(names: list[str], daily: int = 7, weekly: int = 4, monthly: int = 3) -> list[str]:
    runs = sorted({m.group(1) for n in names if (m := RUN.match(n.strip()))}, reverse=True)
    if not runs:
        return []
    when = {r: datetime.strptime(r, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc) for r in runs}
    keep = {runs[0]}
    for count, key in ((daily, lambda d: d.date()), (weekly, lambda d: d.isocalendar()[:2]),
                       (monthly, lambda d: (d.year, d.month))):
        seen = []
        for r in runs:                      # newest first: the first run of each bucket is its newest
            k = key(when[r])
            if k not in seen:
                seen.append(k)
                if len(seen) > count:
                    break
                keep.add(r)
    return [r for r in runs if r not in keep]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--daily", type=int, default=7)
    ap.add_argument("--weekly", type=int, default=4)
    ap.add_argument("--monthly", type=int, default=3)
    ap.add_argument("--as-json", action="store_true", help="print stdin's non-empty lines as one JSON list (for the ledger)")
    a = ap.parse_args(argv)
    if a.as_json:
        print(json.dumps([l.strip() for l in sys.stdin.read().splitlines() if l.strip()]))
        return 0
    for r in plan(sys.stdin.read().splitlines(), a.daily, a.weekly, a.monthly):
        print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
