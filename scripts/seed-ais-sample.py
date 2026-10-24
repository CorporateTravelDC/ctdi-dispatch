#!/usr/bin/env python3
"""
scripts/seed-ais-sample.py -- load a NOAA Marine Cadastre AIS daily file into
ais_sample_positions (pg_schema/0084), the labelled sample behind the
semantic layer's vessel geometry (docs/GEOMETRIC_REASONING_DESIGN_2026-09-17.md
decision #6: maritime seeded from a public sample dataset).

Operator-run. The script never downloads: fetch one daily file by hand from
marinecadastre.gov (AIS_YYYY_MM_DD.zip or .csv) and pass its path. Only rows
inside the same box the live AISHub sweep uses (120 nm around DC,
poller/main.py _check_vessel_aishub) are kept, so a ~300 MB national file
loads a few thousand rows. Never touches vessel_events (the live table).

Idempotent: rows are keyed (dataset, mmsi, observed_at), INSERT OR IGNORE,
so a rerun after a partial load only adds what is missing.

Columns are matched by name, case-insensitively: MMSI, BaseDateTime, LAT,
LON required; SOG, COG, VesselType optional. A file without the required
columns is refused before anything is written.

Usage (from the repo root, through the env loader):
    scripts/with-dispatch-env.sh env PYTHONPATH=src \
        python3 scripts/seed-ais-sample.py /path/to/AIS_2024_01_01.zip --dry-run
    scripts/with-dispatch-env.sh env PYTHONPATH=src \
        python3 scripts/seed-ais-sample.py /path/to/AIS_2024_01_01.zip
Heavy step: run it behind relay-wait-load like any bulk load.
"""
import argparse
import csv
import io
import os
import sys
import zipfile
from datetime import datetime, timezone

CENTER_LAT, CENTER_LON, DIST_NM = 39.0000, -77.0000, 120
DLAT = DIST_NM / 60.0
DLON = DIST_NM / (60.0 * 0.78)
BATCH = 5000
REQUIRED = ("mmsi", "basedatetime", "lat", "lon")


def _open_rows(path: str):
    if path.lower().endswith(".zip"):
        zf = zipfile.ZipFile(path)
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            sys.exit(f"expected exactly one .csv inside {path}, found {len(names)}")
        return csv.reader(io.TextIOWrapper(zf.open(names[0]), encoding="utf-8", newline=""))
    return csv.reader(open(path, encoding="utf-8", newline=""))


def _epoch(s: str) -> float:
    dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _float(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def parse(path: str, dataset: str):
    """Yield (dataset, mmsi, observed_at, lat, lon, sog, cog, vessel_type)
    for each parseable row inside the box; malformed rows are skipped."""
    rows = _open_rows(path)
    header = [h.strip().lower() for h in next(rows)]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        sys.exit(f"{path}: missing required column(s) {', '.join(missing)}; nothing written")
    ix = {h: i for i, h in enumerate(header)}
    for r in rows:
        try:
            lat, lon = float(r[ix["lat"]]), float(r[ix["lon"]])
            mmsi = r[ix["mmsi"]].strip()
            ts = _epoch(r[ix["basedatetime"]])
        except (ValueError, IndexError):
            continue
        if not (mmsi.isdigit() and len(mmsi) <= 9):
            continue
        if abs(lat - CENTER_LAT) > DLAT or abs(lon - CENTER_LON) > DLON:
            continue
        yield (dataset, mmsi.zfill(9), ts, lat, lon,
               _float(r[ix["sog"]]) if "sog" in ix else None,
               _float(r[ix["cog"]]) if "cog" in ix else None,
               (r[ix["vesseltype"]].strip() or None) if "vesseltype" in ix else None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file", help="Marine Cadastre daily AIS file (.zip or .csv)")
    ap.add_argument("--dry-run", action="store_true", help="parse and count only")
    args = ap.parse_args()

    dataset = "marinecadastre:" + os.path.basename(args.file).rsplit(".", 1)[0]
    if args.dry_run:
        n, vessels = 0, set()
        for row in parse(args.file, dataset):
            n += 1
            vessels.add(row[1])
        print(f"{dataset}: {n} positions, {len(vessels)} vessels inside the box (dry run, nothing written)")
        return 0

    from common import db
    n, batch = 0, []
    sql = ("INSERT OR IGNORE INTO ais_sample_positions "
           "(dataset, mmsi, observed_at, lat, lon, sog, cog, vessel_type) "
           "VALUES (?,?,?,?,?,?,?,?)")
    with db.conn() as c:
        for row in parse(args.file, dataset):
            batch.append(row)
            if len(batch) >= BATCH:
                c.executemany(sql, batch)
                n += len(batch)
                batch = []
        if batch:
            c.executemany(sql, batch)
            n += len(batch)
        total = c.execute("SELECT COUNT(*) AS n FROM ais_sample_positions WHERE dataset = ?",
                          (dataset,)).fetchone()["n"]
    print(f"{dataset}: {n} positions offered, {total} rows now stored for this dataset")
    return 0


if __name__ == "__main__":
    sys.exit(main())
