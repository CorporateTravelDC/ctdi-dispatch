"""poller.skills.flight_events_cleanup -- 30-day retention + Nextcloud
archival for flight_events.

Retention policy (2026-07-27, replaces the original design):
  - Rows updated within the last 30 days: retained live.
  - Rows for any flight currently on the active watchlist: retained
    regardless of age (matched correctly via airline+flight_num -- see
    db.get_protected_flight_ids docstring for why the original comparison
    could never match).
  - Everything else: exported in bounded batches (oldest first) to
    gzipped JSONL tarballs, uploaded to the Nextcloud second-brain vault
    (WebDAV, corporatetraveldc/archives/flight_events/), then deleted from
    the live table one batch at a time -- but ONLY after that batch's
    upload has been confirmed to succeed. Batching keeps memory flat
    regardless of backlog size (see BATCH_SIZE comment below).

2026-07-27: this skill has never actually run before today. Two
independent, unrelated bugs, both present since the file was first
written:

  1. No `if __name__ == "__main__":` entry point. Every other skill in
     this directory has one (see e.g. freshness_audit.py). poller/main.py's
     SkillLoop runs each skill as a `python3 <script>.py` subprocess, so a
     script with no entry point imports cleanly, executes nothing, and
     exits 0. That's why "Skill flight-cleanup: ok (rc=0)" logged hourly
     since this file existed, while zero "purged N stale row(s)" lines
     ever appeared anywhere in the poller's log history -- rc=0 only ever
     meant "the interpreter didn't crash importing this file."

  2. Even if it had run, the old db.purge_old_flight_events()'s
     watchlist-protection clause compared flight_events.flight_id (a
     GUFI/UUID) against watchlist_entries.identifier (an ICAO callsign like
     "UAL2670") in a NOT IN clause -- the exact same class of mismatch as
     the _check_flight_fdps_cache bug fixed the same day (see
     fdps_fids_ooo_wiring memory). A GUFI is never in a set of callsigns,
     so the "keep watched flights" exclusion was always a silent no-op.

Together this explains flight_events reaching 219k+ rows / ~6.6GB despite
a "policy" that was supposedly deleting anything over an hour old for as
long as the file existed: it simply never executed.
"""
from __future__ import annotations

import gzip
import io
import json
import logging
import tarfile
from datetime import datetime, timezone

from common import db
from second_brain import webdav_client

log = logging.getLogger("poller.skills.flight_events_cleanup")

RETENTION_DAYS = 30
# 2026-07-27: a live test with cutoff_days=0 (deliberately matching the
# whole table) OOM-killed the poller container -- SELECT * over 220k+ rows
# of raw_json fully materialized into Python dicts blew past its 448m
# memory cap. Processing in bounded batches keeps memory flat regardless
# of how large a first-run backlog turns out to be (e.g. the very first
# time this runs 30 days from now, or after any gap where it didn't run).
BATCH_SIZE = 1000
ARCHIVE_WEBDAV_DIR = f"{webdav_client.BUSINESS_ROOT}/archives/flight_events"


def _build_archive_tarball(rows: list[dict]) -> bytes:
    """Serialize rows to gzipped JSONL, wrap in a tar archive, return the
    tarball's raw bytes. One JSON object per line preserves each row's
    raw_json column intact rather than re-flattening it."""
    jsonl_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=jsonl_buf, mode="wb") as gz:
        for row in rows:
            gz.write((json.dumps(row, default=str) + "\n").encode("utf-8"))
    jsonl_bytes = jsonl_buf.getvalue()

    tar_buf = io.BytesIO()
    jsonl_name = f"flight_events_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl.gz"
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=jsonl_name)
        info.size = len(jsonl_bytes)
        info.mtime = int(datetime.now(timezone.utc).timestamp())
        tar.addfile(info, io.BytesIO(jsonl_bytes))
    return tar_buf.getvalue()


def run() -> None:
    total_archived = 0
    total_bytes = 0
    batch_num = 0
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")

    while True:
        rows = db.export_old_flight_events(cutoff_days=RETENTION_DAYS, limit=BATCH_SIZE)
        if not rows:
            break

        flight_ids = [r["flight_id"] for r in rows]
        tarball = _build_archive_tarball(rows)
        tar_name = f"flight_events_archive_{run_stamp}_batch{batch_num:03d}.tar.gz"
        rel_path = f"{ARCHIVE_WEBDAV_DIR}/{tar_name}"

        try:
            webdav_client.put(rel_path, tarball, content_type="application/gzip")
        except Exception as e:
            # Upload failed -- do NOT delete anything from the live table.
            # Better to retry next run (rows just get re-exported, oldest
            # first) than to lose data that was never actually archived.
            log.error(
                "flight_events_cleanup: Nextcloud upload failed on batch %d (%s), "
                "stopping this run -- %d row(s) in this batch remain live, "
                "%d already archived+deleted earlier in this run",
                batch_num, e, len(rows), total_archived,
            )
            break

        deleted = db.delete_flight_events_by_id(flight_ids)
        total_archived += len(rows)
        total_bytes += len(tarball)
        log.info(
            "flight_events_cleanup: batch %d -- archived %d row(s) (%d bytes) to %s, deleted %d",
            batch_num, len(rows), len(tarball), rel_path, deleted,
        )
        batch_num += 1

        if len(rows) < BATCH_SIZE:
            # Short batch -- protected-set exclusion is applied in SQL
            # before the LIMIT (see db.export_old_flight_events), so this
            # reliably means there's nothing more eligible right now.
            break

    if total_archived:
        log.info(
            "flight_events_cleanup: run complete -- %d row(s) archived (%d bytes total) across %d batch(es)",
            total_archived, total_bytes, batch_num,
        )
    else:
        log.debug("flight_events_cleanup: nothing older than %dd to archive", RETENTION_DAYS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
