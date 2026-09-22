"""
demo.db -- Postgres accessor for the demo-archiver recorder's raw
snapshot table (demo_snapshots, src/common/pg_schema/0057_demo.sql).

2026-09-20 Postgres cutover (operator directive: "everything now in the
live system should be on Postgres... anything currently running live
should have all of the SQLite references removed unless it is either a
historical document or in a retired directory"). This used to be
/var/lib/corporatetraveldc/demo.db, a standalone SQLite file opened
directly by three different call sites (src/demo/recorder.py,
src/poller/skills/second_brain_demo_archiver_daily.py,
src/web/main.py's get_demo_readiness()) -- each with its own
sqlite3.connect() and, in recorder.py's and web/main.py's cases, two
independently-written copies of the same seed-readiness SQL. This module
is the single shared accessor those three now go through, same pattern
as second_brain.index_db.get_conn() wraps db_backend.pg_conn() for the
vault index (pg_schema/0055) and cifp_lookup.py wraps it for CIFP data
(pg_schema/0052).

Deliberately NOT the same table as src/demo/demo_api.py reads.
demo_api.py's DEMO_DB (default /var/lib/corporatetraveldc-demo-source/
demo-source.db) is a separate, still-SQLite, scrubbed/curated file
populated by scripts/scrub-demo-source.py from a read of THIS table (and
of corporatetraveldc.db) -- see that script's own docstring and
scripts/migrate-demo-to-pg.py's module docstring for why that pipeline
is explicitly out of scope for this migration pass (its writer,
scrub-demo-source.py, was flagged for the operator's own decision on a
separate follow-up, not assumed here).

NOT explicitly out of scope, but genuinely unaffected by this module:
demo_api.py itself never opens demo.db (confirmed by grep -- its only
SQLite path is DEMO_DB above), so it needs no code change for this
migration; its one connection to the tables this module owns is
demo.profiles (src/demo/profiles.py), which is migrated separately and
reached only through that module's existing function interface.

VACUUM is dropped entirely on this path (was a SQLite full-file
compaction call in recorder.py's migrate_legacy()/prune() -- Postgres's
autovacuum does this automatically; VACUUM FULL is a manual, blocking,
exclusive-lock operation with no equivalent "run this on every prune
cycle" use here). migrate_legacy() (recorder.py's one-time uncompressed-
row upgrade) is dropped for the same reason its own 2026-09-20 recon
already concluded: confirmed live, 0 of 67,331 rows have compressed=0,
so it was already a permanent no-op before this port and there is
nothing left for a Postgres equivalent to do.
"""
from __future__ import annotations

import zlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from common import db_backend

# Retention tiers used by seed_status() / the /demo/readiness endpoints.
# Kept here (not re-imported from demo.recorder) so this module has no
# dependency on recorder.py -- web/main.py and the archiver skill both
# want this without pulling in recorder.py's requests/zlib polling loop.
RETENTION_TIERS: dict[str, int] = {
    "2w":  14,   # seed target -- always-ready buffer
    "8w":  56,   # bi-monthly
    "12w": 84,   # quarterly (3 months)
    "24w": 168,  # semi-annual (6 months)
    "36w": 252,  # 9 months
    "52w": 364,  # annual (12 months)
}


@contextmanager
def get_conn():
    """Shared connection accessor -- thin wrapper around
    db_backend.pg_conn(), same one-place-that-knows-the-backend pattern
    as second_brain.index_db.get_conn()."""
    with db_backend.pg_conn() as conn:
        yield conn


def ensure_schema() -> None:
    """No-op -- schema is created by pg_schema/0057_demo.sql + Phase 1's
    pg_migrate.py, same pattern every other Postgres-backed module in
    this codebase uses. Kept as a callable (rather than removed) so
    recorder.py's startup sequence keeps its own "init, then seed-status"
    shape unchanged."""
    return None


def last_hash(endpoint: str) -> Optional[str]:
    """Most recent stored payload_hash for `endpoint`, used for write-time
    dedup by recorder.record()."""
    with get_conn() as c:
        row = c.execute(
            "SELECT payload_hash FROM demo_snapshots "
            "WHERE endpoint=? AND payload_hash IS NOT NULL "
            "ORDER BY captured_at DESC LIMIT 1",
            (endpoint,),
        ).fetchone()
    return row["payload_hash"] if row else None


def insert_snapshot(endpoint: str, captured_at: str, payload: bytes,
                     payload_hash: str, compressed: bool = True) -> None:
    with get_conn() as c:
        c.execute(
            "INSERT INTO demo_snapshots "
            "(endpoint, captured_at, payload, payload_hash, compressed) "
            "VALUES (?, ?, ?, ?, ?)",
            (endpoint, captured_at, payload, payload_hash, compressed),
        )


def prune(retention_days: int) -> int:
    """Delete snapshots older than `retention_days`. Returns rows deleted.
    No VACUUM call -- see module docstring."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    with get_conn() as c:
        cur = c.execute("DELETE FROM demo_snapshots WHERE captured_at < ?", (cutoff,))
        return cur.rowcount


def seed_status(seed_target: int, retention_days: int) -> dict:
    """Same shape as the old recorder.seed_status()/web/main.py's inlined
    duplicate of it -- this is now the SINGLE implementation both callers
    (recorder.py's startup log line, web/main.py's /api/v1/demo/readiness)
    use, collapsing the duplication the 2026-09-20 recon flagged.

    db_size_mb now reports demo_snapshots' own Postgres relation size
    (pg_total_relation_size, includes its index) rather than a SQLite
    file's on-disk size -- there is no single "the demo.db file" anymore,
    so this is the closest direct equivalent, not a guess at parity."""
    with get_conn() as c:
        days = c.execute(
            "SELECT COUNT(DISTINCT captured_at::date) FROM demo_snapshots"
        ).fetchone()["count"]
        total = c.execute("SELECT COUNT(*) FROM demo_snapshots").fetchone()["count"]
        oldest = c.execute("SELECT MIN(captured_at) FROM demo_snapshots").fetchone()["min"]
        newest = c.execute("SELECT MAX(captured_at) FROM demo_snapshots").fetchone()["max"]
        size_bytes = c.execute(
            "SELECT pg_total_relation_size('demo_snapshots') AS sz"
        ).fetchone()["sz"]

        tiers: dict[str, dict] = {}
        for label, target_days in RETENTION_TIERS.items():
            cutoff = (datetime.now(timezone.utc) - timedelta(days=target_days)).isoformat()
            avail = c.execute(
                "SELECT COUNT(DISTINCT captured_at::date) FROM demo_snapshots "
                "WHERE captured_at >= ?",
                (cutoff,),
            ).fetchone()["count"]
            tiers[label] = {
                "days_required":  target_days,
                "days_available": avail,
                "ready":          avail >= target_days,
            }

    oldest = (oldest or "")[:10]
    newest = (newest or "")[:10]
    return {
        "seed_days":        days,
        "seed_target":      seed_target,
        "ready":            days >= seed_target,
        "total_snapshots":  total,
        "oldest":           oldest or None,
        "newest":           newest or None,
        "db_size_mb":       round((size_bytes or 0) / 1e6, 1),
        "retention_days":   retention_days,
        "tiers":            tiers,
    }


def fetch_window(cutoff_iso: str) -> list[tuple[str, str, bytes, bool]]:
    """Rows (endpoint, captured_at, payload, compressed) captured at or
    after `cutoff_iso`, ordered oldest-to-newest per endpoint -- backs
    second_brain_demo_archiver_daily.py's rolling ingest window."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT endpoint, captured_at, payload, compressed FROM demo_snapshots "
            "WHERE captured_at >= ? ORDER BY endpoint, captured_at ASC",
            (cutoff_iso,),
        ).fetchall()
    return [(r["endpoint"], r["captured_at"], bytes(r["payload"]), bool(r["compressed"]))
            for r in rows]


def decode_payload(payload: bytes, compressed: bool) -> str:
    """Shared decode helper -- zlib.decompress if compressed, else treat
    as already-plain text/bytes. Same logic every consumer previously
    duplicated inline."""
    raw = zlib.decompress(payload) if compressed else payload
    return raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
