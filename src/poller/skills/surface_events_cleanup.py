"""poller.skills.surface_events_cleanup -- archive + age out surface_movement_events.

Direct sibling of poller/skills/flight_events_cleanup.py, and deliberately
so: same ordering (export -> archive -> confirm -> delete), same batch
discipline, same Nextcloud vault destination. Operator directive 2026-09-23:
"mirror the flight events log behavior that that is using for the surface
movement here as well."

WHY THIS EXISTS
surface_movement_events had NO retention path of any kind. It is absent from
retention_prune.py's _PRUNE_JOBS; flight_events_cleanup covers only
flight_events; audit_log_archive only audit_log. Rows had accumulated since
2026-08-03 unbounded.

WHY ARCHIVE RATHER THAN PRUNE
This table is the ONLY correlation substrate for the SMES safety-bitmask
decoding work -- bit-flip timelines from stdds_safety_status_history
correlated against runway assignments here. A plain DELETE would permanently
destroy that dataset. Archiving to the vault keeps it queryable off-box while
still bounding what sits on the Pi. That constraint is why this is a
cleanup+archive skill and not three lines added to _PRUNE_JOBS.

WHAT A ROW ACTUALLY IS -- read before interpreting an archive
The primary key is (airport, track_id) and track_id comes from the FAA
ASDE-X 12-bit track-number pool (1..4095), which the sensor RECYCLES. So a
row is "the last aircraft to occupy that track slot at that airport", not an
aircraft, and the table saturates at 4095 rows per airport rather than
growing with traffic. Rows older than ~4 days are already a thin
survivorship sample (10-15% of that day's events; the rest were overwritten
in place by later aircraft taking the same slot). This skill preserves what
survived. It cannot recover what upsert already destroyed -- if a complete
record is ever needed, that requires an append-only capture at ingest, which
does not exist today.

RETENTION DEFAULT is 30 days, not the 24-48h that would be defensible for a
purely operational table, precisely because of the bitmask work above.
Override with SURFACE_ARCHIVE_RETENTION_DAYS.

SR-2 exemption: inputs are time-bounded (a retention cutoff), so there is
nothing content-hashable to gate on. No LLM call, so SR-1/SR-2 do not apply.
"""
from __future__ import annotations

import gzip
import io
import json
import logging
import tarfile
from datetime import datetime, timezone

from common import config, db
from common.sr1_log import log_usage
from second_brain import webdav_client

log = logging.getLogger(__name__)
SKILL_NAME = "surface-events-cleanup"

RETENTION_DAYS = int(config.get("SURFACE_ARCHIVE_RETENTION_DAYS", "30") or 30)

# Same batch size as flight_events_cleanup: keeps peak memory flat regardless
# of how large a first-run backlog is. That sibling's own header records a
# live OOM-kill of the poller container from getting this wrong.
BATCH_SIZE = 1000
ARCHIVE_WEBDAV_DIR = f"{webdav_client.BUSINESS_ROOT}/archives/surface_movement_events"


def _build_archive_tarball(rows: list[dict], stamp: str) -> bytes:
    """Gzipped JSONL inside a tar -- byte-identical shape to the
    flight_events archive, so both read back the same way."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for r in rows:
            gz.write((json.dumps(r, default=str) + "\n").encode("utf-8"))
    raw = buf.getvalue()

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=f"surface_movement_events_{stamp}.jsonl.gz")
        info.size = len(raw)
        info.mtime = int(datetime.now(timezone.utc).timestamp())
        tar.addfile(info, io.BytesIO(raw))
    return tar_buf.getvalue()


def run() -> None:
    status = "ok"
    archived = 0
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        batch = 0

        while True:
            rows = db.export_old_surface_movement_events(
                cutoff_days=RETENTION_DAYS, limit=BATCH_SIZE)
            if not rows:
                break

            keys = [(r["airport"], r["track_id"]) for r in rows]
            name = f"surface_movement_events_{stamp}_batch{batch:03d}.tar.gz"
            rel = f"{ARCHIVE_WEBDAV_DIR}/{name}"

            try:
                webdav_client.put(rel, _build_archive_tarball(rows, f"{stamp}_b{batch:03d}"),
                                  content_type="application/gzip")
            except Exception as e:
                # Identical posture to flight_events_cleanup and
                # audit_log_archive: a failed upload is NEVER followed by a
                # delete. Rows stay live and are retried next run.
                log.error("%s: upload failed on batch %d (%s) -- %d row(s) remain live",
                          SKILL_NAME, batch, e, len(rows))
                status = "upload_failed"
                break

            deleted = db.delete_surface_movement_events_by_key(keys)
            archived += deleted
            log.info("%s: batch %d -- archived %d row(s) -> %s, deleted %d",
                     SKILL_NAME, batch, len(rows), rel, deleted)
            batch += 1

            if len(rows) < BATCH_SIZE:
                break

        log.info("%s: done -- %d row(s) archived and aged out (retention %dd)",
                 SKILL_NAME, archived, RETENTION_DAYS)
    except Exception as e:
        log.error("%s: failed -- %s", SKILL_NAME, e)
        status = "error"
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
