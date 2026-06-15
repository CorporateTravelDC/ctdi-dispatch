"""FAA Aircraft Registry + LADD list fetcher.

Downloads weekly from:
  - https://registry.faa.gov/database/ReleasableAircraft.zip  (N-number registry)
  - https://registry.faa.gov/database/LADD_Aircraft.zip       (privacy opt-out list)

FAA publishes both files Sunday night ~midnight ET.  The poller runs this
fetcher weekly (Monday 02:00 ET) via REGISTRY_SWEEP_INTERVAL.

MASTER.txt column layout (fixed-position CSV, comma-delimited, no header):
  0  N-NUMBER          15  LAST ACTION DATE   (YYYYMMDD)
  1  SERIAL NUMBER     16  CERT ISSUE DATE    (YYYYMMDD)
  2  MFR MDL CODE      17  CERTIFICATION
  3  ENG MFR MDL       18  TYPE AIRCRAFT      (1=Glider…7=Rotorcraft)
  4  YEAR MFR          19  TYPE ENGINE        (0=None…9=Electric)
  5  TYPE REGISTRANT   20  STATUS CODE        (V=Valid, D=Dereg…)
  6  NAME              21  MODE S CODE        (octal)
  7  STREET            22  FRACT OWNER
  8  STREET2           23  AIR WORTH DATE
  9  CITY              24..28  OTHER NAMES 1-5
 10  STATE             29  EXPIRATION DATE    (YYYYMMDD)
 11  ZIP CODE          30  UNIQUE ID
 12  REGION            31  KIT MFR
 13  COUNTY            32  KIT MODEL
 14  COUNTRY           33  MODE S CODE HEX    ← what we want
"""

from __future__ import annotations

import csv
import io
import logging
import time
import zipfile
from typing import Generator

import requests

log = logging.getLogger(__name__)

_FAA_REGISTRY_URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"
# NOTE: As of June 2026, LADD_Aircraft.zip redirects to an FAA office page (HTTP 302
# → afb700) — the FAA appears to have discontinued this download endpoint.
# The fetcher handles this gracefully (non-fatal warning). Re-check periodically.
_FAA_LADD_URL     = "https://registry.faa.gov/database/LADD_Aircraft.zip"

# MASTER.txt column indices (0-based)
_COL_N_NUMBER        = 0
_COL_SERIAL          = 1
_COL_MFR_MDL         = 2
_COL_YEAR_MFR        = 4
_COL_NAME            = 6
_COL_CITY            = 9
_COL_STATE           = 10
_COL_LAST_ACTION     = 15
_COL_CERT_ISSUE      = 16
_COL_TYPE_AIRCRAFT   = 18
_COL_TYPE_ENGINE     = 19
_COL_STATUS_CODE     = 20
_COL_EXPIRATION      = 29
_COL_MODE_S_HEX      = 33      # last meaningful column

_BATCH_SIZE = 5_000             # rows per DB commit


_FAA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/releasable_aircraft_download",
    "DNT": "1",
}


def _download_zip(url: str, timeout: int = 300) -> zipfile.ZipFile:
    """Stream-download a ZIP from the FAA and return an in-memory ZipFile."""
    log.info("FAA registry: downloading %s", url)
    resp = requests.get(url, headers=_FAA_HEADERS, timeout=timeout, stream=True)
    resp.raise_for_status()
    buf = io.BytesIO()
    for chunk in resp.iter_content(65536):
        buf.write(chunk)
    buf.seek(0)
    size_mb = buf.tell() / 1_048_576
    log.info("FAA registry: downloaded %.1f MB from %s", size_mb, url)
    return zipfile.ZipFile(buf)


def _parse_master(zf: zipfile.ZipFile) -> Generator[list[dict], None, None]:
    """Yield batches of dicts from MASTER.txt inside the registry ZIP."""
    # The file is sometimes named MASTER.txt or master.txt
    names = zf.namelist()
    master_name = next((n for n in names if n.upper() == "MASTER.TXT"), None)
    if not master_name:
        raise FileNotFoundError(f"MASTER.TXT not found in ZIP; files: {names}")

    with zf.open(master_name) as raw:
        text = io.TextIOWrapper(raw, encoding="latin-1", errors="replace")
        reader = csv.reader(text)
        batch: list[dict] = []
        for row in reader:
            if len(row) < 21:           # minimum viable columns
                continue
            n_num = row[_COL_N_NUMBER].strip()
            if not n_num or n_num.upper() == "N-NUMBER":   # skip header if present
                continue

            hex_val = row[_COL_MODE_S_HEX].strip() if len(row) > _COL_MODE_S_HEX else ""
            batch.append({
                "n_number":        n_num,
                "mode_s_hex":      hex_val.lower() if hex_val else None,
                "serial_number":   row[_COL_SERIAL].strip()      or None,
                "mfr_mdl_code":    row[_COL_MFR_MDL].strip()     or None,
                "year_mfr":        row[_COL_YEAR_MFR].strip()    or None,
                "registrant_name": row[_COL_NAME].strip()        or None,
                "city":            row[_COL_CITY].strip()        or None,
                "state":           row[_COL_STATE].strip()       or None,
                "status_code":     row[_COL_STATUS_CODE].strip() or None,
                "type_aircraft":   row[_COL_TYPE_AIRCRAFT].strip() or None,
                "type_engine":     row[_COL_TYPE_ENGINE].strip() or None,
                "expiration_date": row[_COL_EXPIRATION].strip()  or None,
                "last_action_date":row[_COL_LAST_ACTION].strip() or None,
                "cert_issue_date": row[_COL_CERT_ISSUE].strip()  or None,
            })
            if len(batch) >= _BATCH_SIZE:
                yield batch
                batch = []
        if batch:
            yield batch


def _parse_ladd(zf: zipfile.ZipFile) -> list[str]:
    """Return list of N-numbers from the LADD ZIP."""
    names = zf.namelist()
    # FAA LADD ZIP typically contains LADD_Aircraft.txt or similar
    ladd_name = next(
        (n for n in names if "ladd" in n.lower() or "aircraft" in n.lower()),
        names[0] if names else None,
    )
    if not ladd_name:
        log.warning("FAA LADD: no file found in ZIP")
        return []

    n_numbers: list[str] = []
    with zf.open(ladd_name) as raw:
        text = io.TextIOWrapper(raw, encoding="latin-1", errors="replace")
        reader = csv.reader(text)
        for row in reader:
            if not row:
                continue
            n = row[0].strip()
            if n and n.upper() not in ("N-NUMBER", "NNUMBER"):
                n_numbers.append(n)

    log.info("FAA LADD: %d entries parsed from %s", len(n_numbers), ladd_name)
    return n_numbers


def fetch_faa_registry() -> dict:
    """Download and import FAA registry + LADD into the DB. Returns stats dict."""
    from common import db

    db.init_db_v11()    # idempotent — ensures tables exist

    started = time.time()
    total_upserted = 0

    # ── N-number registry ──────────────────────────────────────────────────
    try:
        zf = _download_zip(_FAA_REGISTRY_URL)
        for batch in _parse_master(zf):
            db.faa_upsert_aircraft(batch)
            total_upserted += len(batch)
        log.info("FAA registry: %d records upserted", total_upserted)
    except Exception as e:
        log.error("FAA registry import failed: %s", e)
        return {"ok": False, "error": str(e)}

    # ── LADD list ─────────────────────────────────────────────────────────
    ladd_count = 0
    try:
        ladd_zf  = _download_zip(_FAA_LADD_URL)
        n_numbers = _parse_ladd(ladd_zf)
        ladd_count = db.faa_upsert_ladd(n_numbers)
        log.info("FAA LADD: %d entries stored", ladd_count)
    except Exception as e:
        log.warning("FAA LADD import failed (non-fatal): %s", e)

    elapsed = time.time() - started
    import datetime
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.faa_registry_meta_set("last_full_import", timestamp)

    stats = {
        "ok": True,
        "registry_upserted": total_upserted,
        "ladd_count": ladd_count,
        "elapsed_sec": round(elapsed, 1),
        "timestamp": timestamp,
    }
    log.info("FAA registry import complete: %s", stats)
    return stats
