"""OpenSky Network aircraft metadata registry fetcher.

Downloads from:
  https://opensky-network.org/datasets/metadata/aircraftDatabase.csv
  (redirects to https://s3.opensky-network.org/data-samples/metadata/aircraftDatabase.csv)

Supplementary/international registry -- covers airframes outside the FAA's
US-only N-number registry (foreign registrations, e.g. G-, D-, F-, VH- etc
prefixes), keyed on ICAO24 hex the same way faa_aircraft_registry is keyed
on mode_s_hex. See common.db opensky_lookup_by_hex() / faa_lookup_by_hex()
for the two-registry lookup pattern.

--- FRESHNESS, confirmed 2026-07-21, corrected 2026-09-01 ---
The rolling aircraftDatabase.csv (_OPENSKY_CSV_URL, what
check_opensky_freshness()/fetch_opensky_registry()'s default check) is NOT
a live-updating feed -- its Last-Modified header has read 2024-11-04 every
time it's been checked, confirmed again live 2026-09-01. A weekly or daily
full pull of that static file would just re-download identical bytes every
cycle for zero new data, wasting bandwidth on a link that's already shown
congestion under load (see memory: SWIM firehose bandwidth notes).

CORRECTION 2026-09-01: the "monthly snapshots... currently on hold" claim
above was wrong, or at least stale -- dated full-database snapshots DO
exist at a different path, same S3 bucket:
  https://s3.opensky-network.org/data-samples/metadata/aircraft-database-complete-YYYY-MM.csv
Confirmed live: aircraft-database-complete-2025-08.csv exists,
Last-Modified 2025-08-22, ~103MB (vs. the rolling file's ~47MB) -- a real,
substantially larger snapshot the rolling endpoint never surfaced.
fetch_opensky_registry() now takes an optional url= override for exactly
this case; check_opensky_freshness() is unchanged (it's specifically about
the rolling file staying static, a separate question from whether a newer
dated snapshot has appeared -- no HEAD-based freshness check exists yet
for the dated-snapshot path, since the exact next filename can't be
predicted the way the rolling URL's staleness can be polled).

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
import sqlite3
import time
from typing import Generator

import requests

log = logging.getLogger(__name__)

_OPENSKY_CSV_URL = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
_BATCH_SIZE = 2000
_TIMEOUT = 300

# 2026-09-01: the aircraft-database-complete-2025-08.csv dated snapshot
# (~103MB, more than double the rolling file's ~47MB) hit Python csv's
# default 131072-byte field-size cap and aborted the import entirely --
# almost certainly a stray unescaped quote somewhere in the larger file
# causing the parser to treat a huge span of the file as one field, not a
# genuinely 128KB+ real value in any column here. Raised generously (10MB)
# rather than sys.maxsize, which can raise OverflowError on some platforms'
# C long size -- 10MB still catches genuinely pathological/corrupt input
# while comfortably clearing whatever tripped this.
csv.field_size_limit(10_000_000)

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
    # 2026-09-01: the dated-snapshot format (aircraft-database-complete-
    # YYYY-MM.csv) renamed this column to icaoAircraftClass -- confirmed
    # same ICAO type-designator values (L1P, L2J, etc.), not a different
    # field. Both keys map to the same output column; whichever the
    # source actually has wins (see _row_to_record's lowercased lookup).
    "icaoaircraftclass": "icao_aircraft_type",
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
    # 2026-09-01: the dated-snapshot format uses camelCase column names
    # (manufacturerIcao, operatorIcao, ...) where the rolling file is
    # all-lowercase -- _FIELD_MAP's keys are lowercase, so match
    # case-insensitively rather than needing every alternate casing
    # spelled out. A no-op for the rolling file (its headers are already
    # lowercase).
    row = {(k or "").lower(): v for k, v in row.items()}
    icao24 = (row.get("icao24") or "").strip().lower()
    if not icao24:
        return None
    rec = {v: (row.get(k) or "").strip() or None for k, v in _FIELD_MAP.items()}
    rec["icao24"] = icao24
    return rec


def _stream_records(resp: requests.Response, quotechar: str = '"') -> Generator[list[dict], None, None]:
    """Parse the CSV response body as a stream, yielding upsert batches
    without holding the whole 94MB+ decoded file in memory at once.

    quotechar: the rolling aircraftDatabase.csv and the dated
    aircraft-database-complete-YYYY-MM.csv snapshots use different CSV
    dialects -- the rolling file is plain/double-quoted (Python csv's
    default), the dated snapshot single-quotes every field
    ('icao24','timestamp',...). Confirmed live 2026-09-01: parsing the
    dated file with the wrong quotechar doesn't error, it just silently
    produces zero valid rows (every field, including "icao24" itself,
    comes through with the quote characters still attached, so no row's
    icao24 lookup ever matches) -- caller must get this right, nothing
    here can detect the mismatch after the fact.
    """
    line_iter = resp.iter_lines(decode_unicode=True)
    reader = csv.DictReader(line_iter, quotechar=quotechar)
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


def fetch_opensky_registry(url: str = _OPENSKY_CSV_URL, quotechar: str = '"') -> dict:
    """Full download + import of the OpenSky aircraft metadata CSV.

    Run this on demand (initial load, or when check_opensky_freshness()
    detects a real change) -- NOT on a tight recurring schedule, see module
    docstring for why.

    url= lets this pull a dated snapshot (aircraft-database-complete-
    YYYY-MM.csv) instead of the default rolling file -- see the module
    docstring's 2026-09-01 correction for why that path exists at all.
    Whatever's passed, its own Last-Modified still gets recorded in
    opensky_registry_meta the same way, so check_opensky_freshness()'s
    comparison stays meaningful for whichever URL was actually last used.

    quotechar= MUST be "'" for a dated snapshot -- see _stream_records's
    docstring; this is not auto-detected. Confirmed live 2026-09-01:
    getting this wrong doesn't raise an error, it silently imports zero
    records (registry_upserted: 0), which the mark-and-sweep safety check
    in opensky_registry_sweep_removed() correctly refuses to act on
    (won't treat "nothing upserted" as "everything genuinely removed"),
    but the caller still needs to notice the 0 and retry with the right
    quotechar rather than assume the run actually did nothing wrong.
    """
    from common import db
    db.init_db_v17()

    started = time.time()
    run_cutoff = started  # see opensky_registry_sweep_removed() docstring
    total_upserted = 0
    remote_lm = None

    try:
        resp = requests.get(url, timeout=_TIMEOUT, stream=True)
        resp.raise_for_status()
        remote_lm = resp.headers.get("Last-Modified")

        for batch in _stream_records(resp, quotechar=quotechar):
            # 2026-09-01: a ~275-batch bulk import (each its own connection/
            # transaction, per common.db.conn()) hit "database is locked"
            # once, from the live poller's own concurrent writes to this
            # same sqlite file -- confirmed by row count jumping from
            # 519,991 to 545,950 before the abort, i.e. real progress, not
            # a clean early failure. common.db.conn()'s connection-level
            # 10s busy-timeout wasn't enough for whatever held the lock
            # that moment. Retrying a handful of times here (not raising
            # the shared connection timeout, which would also slow down
            # unrelated live request-serving code paths) is the properly
            # scoped fix for a bulk job specifically.
            for attempt in range(5):
                try:
                    db.opensky_upsert_aircraft(batch)
                    break
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e).lower() or attempt == 4:
                        raise
                    log.warning("opensky registry: batch upsert hit '%s', retrying (attempt %d/5)", e, attempt + 1)
                    time.sleep(2 * (attempt + 1))
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
