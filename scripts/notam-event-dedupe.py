#!/usr/bin/env python3
"""scripts/notam-event-dedupe.py -- one-off cleanup of GML-id NOTAM rows (2026-10-20).

FNS sent some NOTAMs with an empty <event:year>; aim_parser keyed those by the
GML id ("Event_1_...") and stored the same NOTAM again once a copy WITH the
year arrived (1,786 Event_ rows on 2026-10-20, every one carrying number,
location and issued). The parser now derives the year from issued and a
canonical write absorbs its aliases; this script fixes the rows already stored:

  canonical LOC/YEAR/NUMBER row exists  -> delete the Event_ row
  no canonical row                      -> re-key the Event_ row to it
  no number/location/issued-year        -> left alone (counted)

Counts only, never identifiers.  Run through the dispatch env:
  scripts/with-dispatch-env.sh python3 scripts/notam-event-dedupe.py --dry-run
  scripts/with-dispatch-env.sh python3 scripts/notam-event-dedupe.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common import db  # noqa: E402
from ingest.parsers.aim_parser import notam_year_from_issued  # noqa: E402


def plan(rows: list[dict], existing: set[str]) -> tuple[list[str], list[tuple[str, str, str]], int]:
    """(Event_ ids to delete, (old id, new id, new raw_json) to re-key, left alone)."""
    delete: list[str] = []
    rekey: list[tuple[str, str, str]] = []
    left = 0
    claimed: set[str] = set()
    for r in rows:
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except (TypeError, ValueError):
            left += 1
            continue
        loc, num = raw.get("location") or "", raw.get("number") or ""
        year = raw.get("year") or notam_year_from_issued(raw.get("issued"))
        if not (loc and num and year):
            left += 1
            continue
        canon = f"{loc}/{year}/{num}"
        if canon in existing or canon in claimed:
            delete.append(r["notam_id"])
        else:
            claimed.add(canon)
            rekey.append((r["notam_id"], canon, json.dumps({**raw, "notam_id": canon, "year": year})))
    return delete, rekey, left


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT notam_id, raw_json FROM notams WHERE notam_id LIKE 'Event%'").fetchall()]
        existing = {r["notam_id"] for r in c.execute(
            "SELECT notam_id FROM notams WHERE notam_id NOT LIKE 'Event%'").fetchall()}
    delete, rekey, left = plan(rows, existing)
    print(f"[notam-event-dedupe] Event_ rows: {len(rows)}; delete (canonical exists): {len(delete)}; "
          f"re-key: {len(rekey)}; left alone: {left}" + (" -- DRY RUN, nothing changed" if a.dry_run else ""))
    if a.dry_run:
        return 0
    with db.conn() as c:
        for i in delete:
            c.execute("DELETE FROM notams WHERE notam_id = ?", (i,))
        for old, new, raw in rekey:
            c.execute("UPDATE notams SET notam_id = ?, raw_json = ? WHERE notam_id = ?", (new, raw, old))
        n = c.execute("SELECT count(*) AS n FROM notams WHERE notam_id LIKE 'Event%'").fetchone()["n"]
    print(f"[notam-event-dedupe] done; Event_ rows remaining: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
