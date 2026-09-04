#!/usr/bin/env python3
"""
scripts/backfill_fdps_route_versions.py

One-time seed of fdps_route_versions from the CURRENT flight_events table.
2026-08-31 (Detector D backfill): flight_events is an upsert current-state
table (one row per GUFI, confirmed by an earlier pass -- 910K+ rows means
910K+ flights, not history), so this cannot reconstruct multiple route
VERSIONS per flight the way the document's own backfill idea implies --
there is only ever one route_text per flight to recover, whatever the
flight's raw_json happened to hold at its last write. What this DOES buy:
every currently-in-view DC-area flight gets a version_num=1 baseline row
before the live parser ever sees it again, so the FIRST live route update
after this backfill runs already has something real to diff against
instead of silently treating that first live sighting as version 1 with
no prior (which is what would happen with an empty table).

Deliberately reuses the exact same live code path (parse_fdps_messages +
_in_dc_area + db_swim.upsert_fdps_route_version) rather than
re-implementing route extraction here -- two parsers for one field is
exactly the guessed-field-name class of bug this whole audit day has been
finding and fixing.

Scope: SQL-prefiltered to flight_events.origin/destination in
CORE_AIRPORTS before paying for an XML reparse -- _in_dc_area's
position-based fallback (within 250nm of DCA, not necessarily a
CORE_AIRPORTS pair) is NOT covered by this prefilter, so this is a
best-effort seed of the large majority, not an exhaustive replay. Fine
for a baseline seed; not fine to treat as a complete historical record.

Usage:
    PYTHONPATH=src python3 scripts/backfill_fdps_route_versions.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import time

sys.path.insert(0, "src")

from common import db, db_swim  # noqa: E402
from ingest.parsers import fdps_parser  # noqa: E402
from ingest.parsers.fdps_parser import parse_fdps_messages, _in_dc_area  # noqa: E402
from ingest.parsers.geo_filter import CORE_AIRPORTS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [backfill] %(message)s")
log = logging.getLogger(__name__)

# 2026-08-31: parse_fdps_messages() has a debug-capture side effect
# (writes to /var/lib/corporatetraveldc/fdps_debug*/) that a same-day
# pass already found clobbering LIVE captured samples when invoked
# outside the pytest session that quarantines it (tests/ingest/
# conftest.py's _redirect_live_capture_dirs). This script hits the exact
# same live path -- redirect the same module-level constants to a
# throwaway temp dir before calling the parser, same technique, so a
# backfill run can never overwrite a real live capture.
_CAPTURE_QUARANTINE = tempfile.mkdtemp(prefix="backfill-capture-quarantine-")
fdps_parser._DEBUG_SAMPLE_DIR = _CAPTURE_QUARANTINE
fdps_parser._DEBUG_SAMPLE_DIR_FIXM30 = _CAPTURE_QUARANTINE


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    placeholders = ",".join("?" * len(CORE_AIRPORTS))
    query = f"""
        SELECT flight_id, raw_json FROM flight_events
        WHERE raw_json IS NOT NULL
          AND (origin IN ({placeholders}) OR destination IN ({placeholders}))
    """
    if args.limit:
        query += f" LIMIT {int(args.limit)}"

    params = list(CORE_AIRPORTS) * 2
    start = time.time()
    with db.conn() as c:
        rows = c.execute(query, params).fetchall()
    log.info("candidate rows (origin/destination in CORE_AIRPORTS): %d (query %.1fs)",
             len(rows), time.time() - start)

    seen = 0
    dc_area = 0
    with_route = 0
    inserted = 0
    parse_errors = 0

    for row in rows:
        seen += 1
        try:
            parsed_list = parse_fdps_messages(row["raw_json"].encode("utf-8", errors="replace"))
        except Exception as e:
            parse_errors += 1
            if parse_errors <= 5:
                log.warning("parse error on %s: %s", row["flight_id"], e)
            continue

        for parsed in parsed_list:
            # A batched document's parse can yield sibling flights that
            # aren't the one this row's flight_id names -- only seed the
            # named flight, never a batch-mate that happens to parse out.
            if parsed.get("gufi") and parsed["gufi"] != row["flight_id"]:
                continue
            if not _in_dc_area(parsed):
                continue
            dc_area += 1
            route_text = parsed.get("route_text")
            if not route_text:
                continue
            with_route += 1
            if args.dry_run:
                inserted += 1
                continue
            try:
                is_new, _prev = db_swim.upsert_fdps_route_version(
                    flight_id=row["flight_id"],
                    callsign=parsed.get("callsign"),
                    origin=parsed.get("origin"),
                    destination=parsed.get("destination"),
                    route_text=route_text,
                    source=parsed.get("source"),
                    eta=parsed.get("eta_estimated"),
                )
                if is_new:
                    inserted += 1
            except Exception as e:
                log.warning("upsert error on %s: %s", row["flight_id"], e)

        if seen % 5000 == 0:
            log.info("progress: %d/%d rows, %d dc-area, %d with route_text, %d inserted",
                      seen, len(rows), dc_area, with_route, inserted)

    elapsed = time.time() - start
    log.info("done in %.1fs: %d rows scanned, %d dc-area, %d had route_text, "
              "%d version rows %s, %d parse errors",
              elapsed, seen, dc_area, with_route, inserted,
              "would be inserted (dry-run)" if args.dry_run else "inserted",
              parse_errors)


if __name__ == "__main__":
    main()
