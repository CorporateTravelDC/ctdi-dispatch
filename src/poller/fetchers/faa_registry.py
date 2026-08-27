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


# Resume/retry tuning for _download_zip(). registry.faa.gov has repeatedly
# dropped the connection mid-transfer on the ~73MB registry file
# (requests.exceptions.ChunkedEncodingError wrapping IncompleteRead).
# Confirmed 2026-07-13: happened on both a scheduled run and a manual
# retry, and again repeatedly during hardening testing — connections
# reliably survive only ~7MB before dropping, so a fixed attempt count
# (originally 6 retries) ran out before the 73MB file completed. Bounded
# by wall-clock elapsed time instead, with short backoff, since failures
# are frequent (every 10-20s) rather than needing long cool-off periods.
_MAX_ELAPSED_SEC = 2400          # give up resuming after 40 min total (observed real-world throughput ~40-50KB/s effective, see 2026-07-13 testing)
_INITIAL_BACKOFF_SEC = 3
_MAX_BACKOFF_SEC = 30


def _download_zip(url: str, timeout: int = 300) -> zipfile.ZipFile:
    """Stream-download a ZIP from the FAA, resuming on connection drops.

    Uses a Range header to resume from the last byte received rather than
    restarting from scratch, with exponential backoff between attempts.
    Falls back to a full restart if the server doesn't honor the Range
    request (no 206 response).
    """
    buf = io.BytesIO()
    attempt = 0
    backoff = _INITIAL_BACKOFF_SEC
    start_time = time.time()

    while True:
        attempt += 1
        elapsed = time.time() - start_time
        headers = dict(_FAA_HEADERS)
        resume_from = buf.tell()
        if resume_from:
            headers["Range"] = f"bytes={resume_from}-"
            log.info(
                "FAA registry: resuming %s at byte %d (attempt %d, %ds elapsed)",
                url, resume_from, attempt, int(elapsed),
            )
        else:
            log.info(
                "FAA registry: downloading %s (attempt %d)",
                url, attempt,
            )

        try:
            resp = requests.get(url, headers=headers, timeout=timeout, stream=True)
            resp.raise_for_status()

            if resume_from and resp.status_code != 206:
                log.warning(
                    "FAA registry: server did not honor Range resume "
                    "(got HTTP %d, expected 206) — restarting from scratch",
                    resp.status_code,
                )
                buf = io.BytesIO()

            for chunk in resp.iter_content(65536):
                buf.write(chunk)

            size_mb = buf.tell() / 1_048_576
            log.info(
                "FAA registry: downloaded %.1f MB from %s (%d attempt%s)",
                size_mb, url, attempt, "" if attempt == 1 else "s",
            )
            buf.seek(0)
            return zipfile.ZipFile(buf)

        except (
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as e:
            elapsed = time.time() - start_time
            if elapsed > _MAX_ELAPSED_SEC:
                log.error(
                    "FAA registry: giving up on %s after %d attempts / %ds "
                    "(%.1f MB downloaded): %s",
                    url, attempt, int(elapsed), buf.tell() / 1_048_576, e,
                )
                raise
            log.warning(
                "FAA registry: download interrupted at %.1f MB "
                "(attempt %d, %ds elapsed): %s — retrying in %ds",
                buf.tell() / 1_048_576, attempt, int(elapsed), e, backoff,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SEC)


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


def _parse_acftref(zf: zipfile.ZipFile) -> Generator[list[dict], None, None]:
    """Yield batches of dicts from ACFTREF.txt inside the SAME registry ZIP
    MASTER.txt comes from -- no separate download. Added 2026-08-02 to
    decode faa_aircraft_registry.mfr_mdl_code locally instead of requiring
    an external WebFetch to registry.faa.gov per lookup (done by hand for
    N39FE earlier the same day this was built).

    Column layout confirmed against a live download (comma-delimited, one
    header row "CODE,MFR,MODEL,...", trailing comma on every data row,
    UTF-8 BOM on the header line -- hence utf-8-sig decoding here instead
    of latin-1 like MASTER.txt uses):
      0 CODE  1 MFR  2 MODEL  3 TYPE-ACFT  4 TYPE-ENG  5 AC-CAT
      6 BUILD-CERT-IND  7 NO-ENG  8 NO-SEATS  9 AC-WEIGHT  10 SPEED
    """
    names = zf.namelist()
    ref_name = next((n for n in names if n.upper() == "ACFTREF.TXT"), None)
    if not ref_name:
        log.warning("FAA registry: ACFTREF.TXT not found in ZIP; files: %s", names)
        return

    with zf.open(ref_name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace")
        reader = csv.reader(text)
        batch: list[dict] = []
        for row in reader:
            if len(row) < 11:
                continue
            code = row[0].strip()
            if not code or code.upper() == "CODE":   # header row
                continue
            batch.append({
                "code":         code,
                "manufacturer": row[1].strip() or None,
                "model":        row[2].strip() or None,
                "type_acft":    row[3].strip() or None,
                "type_engine":  row[4].strip() or None,
                "ac_category":  row[5].strip() or None,
                "no_engines":   row[7].strip() or None,
                "no_seats":     row[8].strip() or None,
                "ac_weight":    row[9].strip() or None,
                "speed":        row[10].strip() or None,
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
    # Captured before any upsert -- see faa_registry_sweep_removed() docstring.
    # Every row touched by this run gets updated_at >= run_cutoff; anything
    # still older after the run genuinely dropped out of the source file.
    run_cutoff = started
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

    # ── ACFTREF reference table (mfr_mdl_code -> manufacturer/model) ────────
    # Same zip, no new download. Non-fatal if it fails -- the registry
    # import itself already succeeded and shouldn't be blocked by this.
    acftref_count = 0
    try:
        for batch in _parse_acftref(zf):
            db.faa_acftref_upsert(batch)
            acftref_count += len(batch)
        log.info("FAA ACFTREF: %d reference records upserted", acftref_count)
    except Exception as e:
        log.warning("FAA ACFTREF import failed (non-fatal): %s", e)

    # ── LADD list ─────────────────────────────────────────────────────────
    ladd_count = 0
    try:
        ladd_zf  = _download_zip(_FAA_LADD_URL)
        n_numbers = _parse_ladd(ladd_zf)
        if not n_numbers:
            # 2026-08-25 (Opus blind review C-31/C-14): an empty parse used
            # to flow straight into db.faa_upsert_ladd([]), which wiped the
            # entire privacy opt-out list to zero with nothing louder than
            # an info-level "0 entries stored" line -- confirmed live, the
            # table had been sitting empty. db.faa_upsert_ladd() now
            # refuses an empty replacement on its own (defense in depth),
            # but the real signal belongs here at ERROR, not swallowed into
            # the same "non-fatal" bucket as an ordinary download hiccup --
            # a persistently empty LADD source is a privacy-protection
            # outage, not routine noise.
            log.error(
                "FAA LADD: parse produced zero entries -- privacy opt-out "
                "list NOT updated (existing entries preserved). See the "
                "_FAA_LADD_URL redirect note at the top of this file: the "
                "FAA appears to have discontinued this endpoint."
            )
        ladd_count = db.faa_upsert_ladd(n_numbers)
        log.info("FAA LADD: %d entries stored", ladd_count)
    except Exception as e:
        log.warning("FAA LADD import failed (non-fatal): %s", e)

    # ── Sweep removed/deregistered aircraft ─────────────────────────────────
    # Added 2026-07-21. CORRECTED 2026-08-26 (Opus blind review C-7): this
    # comment used to claim the try/except above already guards this --
    # false. That only catches an exception; a 200-OK response that parses
    # to zero batches raises nothing, so total_upserted stays 0 and this
    # sweep ran anyway with run_cutoff predating every row, wiping the
    # entire registry. The real guard now lives in db._safe_mark_and_sweep()
    # (refuses to delete if it would empty a non-empty table), not here.
    removed_count = 0
    try:
        removed_count = db.faa_registry_sweep_removed(run_cutoff)
        if removed_count:
            log.info("FAA registry: swept %d removed/deregistered records", removed_count)
    except Exception as e:
        log.warning("FAA registry sweep failed (non-fatal): %s", e)

    elapsed = time.time() - started
    import datetime
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.faa_registry_meta_set("last_full_import", timestamp)

    stats = {
        "ok": True,
        "registry_upserted": total_upserted,
        "acftref_count": acftref_count,
        "ladd_count": ladd_count,
        "removed_count": removed_count,
        "elapsed_sec": round(elapsed, 1),
        "timestamp": timestamp,
    }
    log.info("FAA registry import complete: %s", stats)
    return stats
