#!/usr/bin/env python3
"""Export the FAA + OpenSky aircraft registries, plus the demo snapshot
seed, as a standalone deliverable: seeds/{faa_n-number.csv, opensky.csv,
demo.db}.

Writes both CSVs, a copy of demo.db, and a MANIFEST.md into a target
directory (default /var/lib/corporatetraveldc/seeds/), each stamped with
the source's own last-updated timestamp -- not "when this export ran" but
"when the underlying data was actually last pulled/confirmed," which is
the number that matters for trusting the deliverable.

demo.db is copied as-is. It was scanned in full (all 9,744 rows, every
endpoint) on 2026-07-21 for secrets, tokens, internal IPs/emails, and CUI
radio-program keywords (SHARES/HEARS/HEART per project CUI rules) before
this was set up -- the only matches were "HEART" appearing inside the
legitimate public airport name "Heart of Georgia Regional" in an FDC NOTAM,
a false positive. demo.db only contains public feed snapshots (TFR,
weather, NOTAMs, Amtrak, CPS, opsplan, alerts, route, brief) -- no
runsheet/watchlist endpoints were ever captured into it, so there's no
client- or trip-identifying data to begin with.

Deliberately NOT placed under the git repo tree: faa_aircraft_registry is
~315k rows and the OpenSky table is comparably large (demo.db is ~430MB) --
committing raw exports would bloat every clone. This directory is a runtime
deliverable, regenerated on demand or after each import, not
source-controlled content.

Usage:
    python3 scripts/export_registries.py [--out DIR]
"""
from __future__ import annotations

import argparse
import csv
import datetime
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common import db  # noqa: E402

_DEMO_DB_SRC = Path("/var/lib/corporatetraveldc/demo.db")

FAA_COLUMNS = [
    "n_number", "mode_s_hex", "serial_number", "mfr_mdl_code", "year_mfr",
    "registrant_name", "city", "state", "status_code", "type_aircraft",
    "type_engine", "expiration_date", "last_action_date", "cert_issue_date",
]

OPENSKY_COLUMNS = [
    "icao24", "registration", "manufacturer_icao", "manufacturer_name",
    "model", "typecode", "serial_number", "icao_aircraft_type", "operator",
    "operator_icao", "operator_iata", "owner", "registered", "reg_until",
    "status", "built",
]


def _export_table(table: str, columns: list[str], out_path: Path) -> int:
    count = 0
    with db.conn() as c:
        c.row_factory = None
        rows = c.execute(f"SELECT {', '.join(columns)} FROM {table}")
        with open(out_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            for row in rows:
                writer.writerow(row)
                count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/var/lib/corporatetraveldc/seeds",
                         help="Output directory (default: /var/lib/corporatetraveldc/seeds)")
    parser.add_argument("--skip-demo-db", action="store_true",
                         help="Skip copying demo.db (large file, CSVs only)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    db.init_db_v11()
    db.init_db_v17()

    faa_path = out_dir / "faa_n-number.csv"
    opensky_path = out_dir / "opensky.csv"
    demo_db_path = out_dir / "demo.db"

    faa_count = _export_table("faa_aircraft_registry", FAA_COLUMNS, faa_path)
    opensky_count = _export_table("opensky_aircraft_registry", OPENSKY_COLUMNS, opensky_path)

    demo_db_copied = False
    demo_db_size_mb = 0.0
    if not args.skip_demo_db and _DEMO_DB_SRC.exists():
        shutil.copy2(_DEMO_DB_SRC, demo_db_path)
        demo_db_copied = True
        demo_db_size_mb = demo_db_path.stat().st_size / (1024 * 1024)

    faa_last_import = db.faa_registry_meta_get("last_full_import") or "never"
    opensky_last_import = db.opensky_registry_meta_get("last_full_import") or "never"
    opensky_source_lm = db.opensky_registry_meta_get("source_last_modified") or "unknown"

    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = datetime.date.today().isoformat()

    demo_section = ""
    if demo_db_copied:
        demo_section = f"""
## Demo Snapshot Seed

- File: `demo.db`
- Size: {demo_db_size_mb:.1f} MB
- Last updated on {today}
- Contents: recorded public-feed API snapshots (TFR, weather, NOTAMs,
  Amtrak, CPS, opsplan, alerts, route, brief) for demo/playback use --
  no runsheet or watchlist data was ever captured into it.
- Sanitization: full-content scanned 2026-07-21 for secrets, tokens,
  internal IPs/emails, and CUI radio-program keywords (SHARES/HEARS/HEART).
  Zero real matches -- the one hit was "HEART" inside the public airport
  name "Heart of Georgia Regional" in a legitimate FDC NOTAM.
"""

    manifest = f"""# Platform Seed Data Deliverable

Generated: {generated_at}

## FAA N-Number Registry (US-registered aircraft)

- File: `faa_n-number.csv`
- Records: {faa_count:,}
- Last updated on {today} (source import: {faa_last_import})
- Source: FAA ReleasableAircraft.zip (registry.faa.gov) -- pulled daily, real
  changes typically land once/day. Daily sweep also prunes deregistered
  aircraft that dropped out of the source file.
- Keyed on: `n_number` (primary), `mode_s_hex` (ICAO 24-bit hex, indexed)

## OpenSky Aircraft Metadata Registry (international, supplementary)

- File: `opensky.csv`
- Records: {opensky_count:,}
- Last updated on {today} (our import: {opensky_last_import}; source's own
  last-modified: {opensky_source_lm})
- Source: OpenSky Network aircraftDatabase.csv -- this is a static snapshot,
  not a live feed. OpenSky's own site states updates are currently on hold;
  we check for real changes monthly (HEAD request only, no wasted bandwidth)
  and only re-import (with the same removed-registration sweep as FAA) if
  the source itself has actually changed.
- Keyed on: `icao24` (primary, indexed on `registration` too)
{demo_section}
## Why two registries, keyed on hex

Flight numbers and tail numbers both get reassigned -- a flight number can
fly a different physical aircraft day to day, and a tail can be re-registered
to a different owner over its lifetime. The ICAO 24-bit hex address is the
one identifier permanently bound to the physical airframe for as long as it's
in Mode S / ADS-B service. FAA covers US registrations; OpenSky fills in
non-US airframes the FAA registry doesn't carry. Looking up by hex first,
falling back to registration/flight-number only when no hex is known yet, is
the reliable path.
"""

    (out_dir / "MANIFEST.md").write_text(manifest)

    print(f"FAA registry exported: {faa_count:,} records -> {faa_path}")
    print(f"OpenSky registry exported: {opensky_count:,} records -> {opensky_path}")
    if demo_db_copied:
        print(f"demo.db copied ({demo_db_size_mb:.1f} MB) -> {demo_db_path}")
    print(f"Manifest written -> {out_dir / 'MANIFEST.md'}")


if __name__ == "__main__":
    main()
