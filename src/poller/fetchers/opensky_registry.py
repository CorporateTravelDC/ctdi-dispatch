"""OpenSky Network aircraft metadata registry fetcher.

Downloads from:
  https://opensky-network.org/datasets/metadata/aircraftDatabase.csv
  (redirects to https://s3.opensky-network.org/data-samples/metadata/aircraftDatabase.csv)

Supplementary/international registry -- covers airframes outside the FAA's
US-only N-number registry (foreign registrations, e.g. G-, D-, F-, VH- etc
prefixes), keyed on ICAO24 hex the same way faa_aircraft_registry is keyed
on mode_s_hex. See common.db opensky_lookup_by_hex() / faa_lookup_by_hex()
for the two-registry lookup pattern.

--- FRESHNESS, confirmed 2026-07-21 ---
This is NOT a live-updating feed. OpenSky's own scientific-datasets page
states "Monthly snapshots are also available but updates are currently on
hold," and the file's own Last-Modified header (checked via HEAD request)
reads 2024-11-04 -- 20+ months old at time of writing. A weekly or daily
full pull of a static 94MB file would just re-download identical bytes
every cycle for zero new data, wasting bandwidth on a link that's already
shown congestion under load (see memory: SWIM firehose bandwidth notes).

So this fetcher is NOT wired into poller's recurring interval loop the way
FAA_REGISTRY_INTERVAL is. Instead:
  - fetch_opensky_registry()   does the actual full import -- run manually /
                                on demand, or by check_opensky_freshness()
                                when it detects the source has changed.
  - check_opensky_freshness()  cheap HEAD-only check (~0 bytes transferred);
                                compares the remote Last-Modified header
                                against what's stored in opensky_registry_meta
                                and only triggers a real download if it has
                                changed. This is the piece safe to run on a
                                recurring schedule (e.g. poller could call it
                                monthly) without wasting bandwidth.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from typing import Generator

import requests

log = logging.getLogger(__name__)

_OPENSKY_CSV_URL = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
_BATCH_SIZE = 2000
_TIMEOUT = 300

# CSV column order confirmed 2026-07-21 via direct range-request sample:
# icao24,registration,manufacturericao,manufacturername,model,typecode,
# serialnumber,linenumber,icaoaircrafttype,operator,operatorcallsign,
# operatoricao,operatoriata,owner,testreg,registered,reguntil,status,built,
# firstflightdate,seatconfiguration,engines,modes,adsb,acars,notes,
# categoryDescription
_FIELD_MAP = {
    "icao24":            "icao24",
    "registration":      "registration",
    "manufacturericao":  "manufacturer_icao",
    "manufacturername":  "manufacturer_name",
    "model":             "model",
    "typecode":          "typecode",
    "serialnumber":      "serial_number",
    "icaoaircrafttype":  "icao_aircraft_type",
    "operator":          "operator",
    "operatoricao":      "operator_icao",
    "operatoriata":      "operator_iata",
    "owner":              "owner",
    "registered":        "registered",
    "reguntil":          "reg_until",
    "status":            "status",
    "built":             "built",
}


def _head_last_modified() -> str | None:
    """Cheap freshness probe -- HEAD only, no body transferred."""
    try:
        resp = requests.head(_OPENSKY_CSV_URL, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        return resp.headers.get("Last-Modified")
    except Exception as e:
        log.warning("opensky registry: HEAD freshness check failed: %s", e)
        return None


def check_opensky_freshness() -> dict:
    """Compare remote Last-Modified against stored value. Triggers a full
    import only if the source has actually changed since our last pull.
    Safe to call on a recurring (e.g. monthly) schedule -- costs one HEAD
    request, no bulk download unless something changed.
    """
    from common import db
    db.init_db_v17()

    remote_lm = _head_last_modified()
    if remote_lm is None:
        return {"ok": False, "error": "HEAD request failed"}

    stored_lm = db.opensky_registry_meta_get("source_last_modified")
    if remote_lm == stored_lm:
        log.info("opensky registry: source unchanged (Last-Modified=%s), skipping re-import", remote_lm)
        return {"ok": True, "changed": False, "source_last_modified": remote_lm}

    log.info("opensky registry: source changed (was %s, now %s) -- running full import",
              stored_lm, remote_lm)
    stats = fetch_opensky_registry()
    stats["changed"] = True
    return stats


def _row_to_record(row: dict) -> dict | None:
    icao24 = (row.get("icao24") or "").strip().lower()
    if not icao24:
        return None
    rec = {v: (row.get(k) or "").strip() or None for k, v in _FIELD_MAP.items()}
    rec["icao24"] = icao24
    return rec


def _stream_records(resp: requests.Response) -> Generator[list[dict], None, None]:
    """Parse the CSV response body as a stream, yielding upsert batches
    without holding the whole 94MB+ decoded file in memory at once."""
    line_iter = resp.iter_lines(decode_unicode=True)
    reader = csv.DictReader(line_iter)
    batch: list[dict] = []
    for row in reader:
        rec = _row_to_record(row)
        if rec is None:
            continue
        batch.append(rec)
        if len(batch) >= _BATCH_SIZE:
            yield batch
            batch = []
    if batch:
        yield batch


def fetch_opensky_registry() -> dict:
    """Full download + import of the OpenSky aircraft metadata CSV.

    Run this on demand (initial load, or when check_opensky_freshness()
    detects a real change) -- NOT on a tight recurring schedule, see module
    docstring for why.
    """
    from common import db
    db.init_db_v17()

    started = time.time()
    run_cutoff = started  # see opensky_registry_sweep_removed() docstring
    total_upserted = 0
    remote_lm = None

    try:
        resp = requests.get(_OPENSKY_CSV_URL, timeout=_TIMEOUT, stream=True)
        resp.raise_for_status()
        remote_lm = resp.headers.get("Last-Modified")

        for batch in _stream_records(resp):
            db.opensky_upsert_aircraft(batch)
            total_upserted += len(batch)
        log.info("opensky registry: %d records upserted", total_upserted)
    except Exception as e:
        log.error("opensky registry import failed: %s", e)
        return {"ok": False, "error": str(e)}

    removed_count = 0
    try:
        removed_count = db.opensky_registry_sweep_removed(run_cutoff)
        if removed_count:
            log.info("opensky registry: swept %d removed records", removed_count)
    except Exception as e:
        log.warning("opensky registry sweep failed (non-fatal): %s", e)

    elapsed = time.time() - started
    import datetime
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.opensky_registry_meta_set("last_full_import", timestamp)
    if remote_lm:
        db.opensky_registry_meta_set("source_last_modified", remote_lm)

    stats = {
        "ok": True,
        "registry_upserted": total_upserted,
        "removed_count": removed_count,
        "elapsed_sec": round(elapsed, 1),
        "timestamp": timestamp,
        "source_last_modified": remote_lm,
    }
    log.info("opensky registry import complete: %s", stats)
    return stats
