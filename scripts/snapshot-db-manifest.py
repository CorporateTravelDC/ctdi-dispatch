#!/usr/bin/env python3
"""snapshot-db-manifest.py -- point-in-time content-hash manifest of the
dispatch database, covering both backends while both are populated with
identical data (the pre-cutover checkpoint window).

For each of the 65 tables in common.db_backend.PG_TABLES, computes a
streaming (memory-bounded) rolling content hash plus a row count, from
SQLite and from Postgres independently, in stable primary-key order. Two
tables with identical content in identical order produce identical hashes
-- this is a real, evidence-based confirmation of parity, not a repeat of
the row-count-only check the migration script already does, and it is
cheap enough (one sequential pass per table, one sha256 object updated per
row, no .fetchall() of anything) to be safe to run at any time.

This is deliberately separate from scripts/sign-manifest.sh's repo-tree
manifest -- that one attests the CODE; this one attests the DATA -- and is
signed the same way, with the same agent key, by
scripts/sign-db-manifest.sh, and stored under manifests/ (git-tracked,
separate from the database files themselves) so the attestation survives
independently of anything that later happens to the data it describes.

Usage:
  python3 scripts/snapshot-db-manifest.py [--out manifests/DB_MANIFEST.json]

2026-09-19 addition: --tables/--sqlite-db/--sqlite-only/--purpose let this
same tool produce a SQLite-only source snapshot of an arbitrary table set
from an arbitrary SQLite file (e.g. the 11 reference tables + second-brain
index tables, pre-copy, from corporatetraveldc.db / second_brain_index.db)
-- the "checkpoint 1" origin-state artifact for the reference-table +
second-brain Postgres migration (docs/POSTGRES_MIGRATION.md §2). Default
behavior (no new flags) is unchanged: dual-hash PG_TABLES against both
backends, exactly as before.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("NEXTCLOUD_ADMIN_USER", "corporatetraveldc")

import psycopg  # noqa: E402
from common import config, db_backend  # noqa: E402

# 2026-09-20 incident: this script's own unbounded (statement_timeout=0)
# scan of flight_events ran unsupervised for well over an hour, undetected
# because thermal-ingest-guard only throttles ingest -- it has no lever
# over an ad-hoc script like this one -- and was a major contributor to
# that night's full-box overload (load1 hit 85.6, containers failed to
# spawn, the platform went dark for ~7 hours until a physical power
# cycle). Every table-hash pass is now bounded, both sides, to whichever
# of these trips first. Never remove or widen these without a replacement
# safety net.
TABLE_TIMEOUT_SECONDS = 15 * 60
LOAD1_MAX = 35.0


class BoundsExceeded(RuntimeError):
    """A single table's hash pass exceeded TABLE_TIMEOUT_SECONDS or LOAD1_MAX."""


def _load1() -> float:
    try:
        with open("/proc/loadavg") as f:
            return float(f.read().split()[0])
    except OSError:
        return 0.0


def _row_hash(values) -> str:
    """Same canonicalization as migrate-sqlite-to-pg.py's _row_hash --
    str() every value so both engines' native type differences (float vs
    Decimal, etc.) collapse to the same representation for identical
    declared-type columns."""
    canon = "\x1f".join("\x00" if v is None else str(v) for v in values)
    return hashlib.sha256(canon.encode("utf-8", "surrogateescape")).hexdigest()


def sqlite_pk_columns(sconn: sqlite3.Connection, table: str) -> list[str]:
    rows = sconn.execute(f"PRAGMA table_info({table})").fetchall()
    pk = [(r[5], r[1]) for r in rows if r[5]]
    pk.sort()
    return [name for _, name in pk]


def sqlite_columns(sconn: sqlite3.Connection, table: str) -> list[str]:
    rows = sconn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def hash_sqlite_table(sconn: sqlite3.Connection, table: str) -> tuple[int, str]:
    cols = sqlite_columns(sconn, table)
    pk_cols = sqlite_pk_columns(sconn, table) or cols[:1]
    cols_sql = ", ".join(cols)
    order_sql = ", ".join(pk_cols)
    t0 = time.monotonic()

    def _progress() -> int:
        # Called by sqlite3 every 200k VM instructions -- fires during the
        # initial sort/scan too, not just once rows start streaming, so it
        # bounds the whole query, not just the row loop.
        if time.monotonic() - t0 > TABLE_TIMEOUT_SECONDS or _load1() >= LOAD1_MAX:
            return 1
        return 0

    sconn.set_progress_handler(_progress, 200_000)
    try:
        cur = sconn.execute(f"SELECT {cols_sql} FROM {table} ORDER BY {order_sql}")
        roller = hashlib.sha256()
        n = 0
        for row in cur:
            roller.update(_row_hash(row).encode("ascii"))
            n += 1
        cur.close()
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e).lower():
            raise BoundsExceeded(
                f"{table}: SQLite scan aborted after {time.monotonic() - t0:.0f}s "
                f"(load1={_load1():.1f}) -- exceeded {TABLE_TIMEOUT_SECONDS}s or "
                f"load1>={LOAD1_MAX} bound"
            ) from e
        raise
    finally:
        sconn.set_progress_handler(None, 0)
    return n, roller.hexdigest()


def hash_pg_table(pconn, table: str) -> tuple[int, str]:
    with pconn.cursor() as cur:
        cur.execute(
            """
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = %s::regclass AND i.indisprimary
            ORDER BY array_position(i.indkey, a.attnum)
            """,
            (table,),
        )
        pk_cols = [r[0] for r in cur.fetchall()]
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        cols = [r[0] for r in cur.fetchall()]
        pk_cols = pk_cols or cols[:1]
        cols_sql = ", ".join(cols)
        order_sql = ", ".join(pk_cols)
        t0 = time.monotonic()
        cur.execute(f"SELECT {cols_sql} FROM {table} ORDER BY {order_sql}")
        roller = hashlib.sha256()
        n = 0
        for row in cur:
            roller.update(_row_hash(row).encode("ascii"))
            n += 1
            if n % 200_000 == 0 and (
                time.monotonic() - t0 > TABLE_TIMEOUT_SECONDS or _load1() >= LOAD1_MAX
            ):
                raise BoundsExceeded(
                    f"{table}: Postgres scan aborted after {time.monotonic() - t0:.0f}s, "
                    f"{n} rows (load1={_load1():.1f}) -- exceeded {TABLE_TIMEOUT_SECONDS}s "
                    f"or load1>={LOAD1_MAX} bound"
                )
        return n, roller.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="manifests/DB_MANIFEST.json")
    ap.add_argument("--tables", default=None,
                     help="Comma-separated table names (default: the 65 in PG_TABLES).")
    ap.add_argument("--sqlite-db", default=None,
                     help="Override the source SQLite file (default: config.db_path()).")
    ap.add_argument("--sqlite-only", action="store_true",
                     help="Skip Postgres entirely -- source-only content snapshot "
                          "(the pre-copy checkpoint use case; no pg_count/pg_hash/match).")
    ap.add_argument("--purpose", default=None,
                     help="Override the manifest's purpose/description field.")
    args = ap.parse_args()

    sconn = sqlite3.connect(args.sqlite_db or config.db_path())
    pconn = None
    if not args.sqlite_only:
        pconn = psycopg.connect(db_backend._pg_conninfo(), autocommit=True)  # noqa: SLF001
        # config/postgresql.conf sets statement_timeout=60s platform-wide to
        # protect live app traffic -- correct for the pool, wrong for this
        # script's own one-off streaming read of an entire table (observed
        # live 2026-09-19: flight_events' ORDER BY hit QueryCanceled at 60s).
        # Scoped to this script's own standalone connection only; never
        # touches the shared pool or postgresql.conf itself. Bounded to
        # TABLE_TIMEOUT_SECONDS, not disabled -- see that constant's
        # header comment for why an unbounded version of this exact line
        # was a major contributor to the 2026-09-20 outage.
        pconn.execute(f"SET statement_timeout = {TABLE_TIMEOUT_SECONDS * 1000}")

    tables = sorted(args.tables.split(",")) if args.tables else sorted(db_backend.PG_TABLES)
    entries = []
    mismatches = []
    t0 = time.time()
    for i, table in enumerate(tables, 1):
        try:
            s_n, s_hash = hash_sqlite_table(sconn, table)
            if args.sqlite_only:
                entries.append({"table": table, "sqlite_count": s_n, "sqlite_hash": s_hash})
                print(f"[{i}/{len(tables)}] {table:45s} sqlite={s_n:>9}", file=sys.stderr)
                continue
            p_n, p_hash = hash_pg_table(pconn, table)
        except BoundsExceeded as e:
            print(f"\nXX ABORTED [{i}/{len(tables)}] {e}", file=sys.stderr)
            print("XX Not writing a manifest -- this run is partial, not a valid "
                  "snapshot. Re-run once load is clear, or split --tables into "
                  "smaller batches for anything this large.", file=sys.stderr)
            return 2
        match = (s_n == p_n) and (s_hash == p_hash)
        entries.append({
            "table": table,
            "sqlite_count": s_n,
            "sqlite_hash": s_hash,
            "pg_count": p_n,
            "pg_hash": p_hash,
            "match": match,
        })
        flag = "OK" if match else "MISMATCH"
        print(f"[{i}/{len(tables)}] {table:45s} sqlite={s_n:>9} pg={p_n:>9}  {flag}", file=sys.stderr)
        if not match:
            mismatches.append(table)

    default_purpose = ("Pre-cutover checkpoint: SQLite/Postgres content parity, "
                        "signed baseline for future incremental integrity audits.")
    if args.sqlite_only:
        default_purpose = "Source-only content snapshot (pre-copy checkpoint)."
    manifest = {
        "kind": "corporatetraveldc-db-content-manifest",
        "version": 1,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": args.purpose or default_purpose,
        "source_sqlite_db": args.sqlite_db or config.db_path(),
        "sqlite_only": args.sqlite_only,
        "table_count": len(tables),
        "all_match": (not mismatches) if not args.sqlite_only else None,
        "mismatched_tables": mismatches if not args.sqlite_only else None,
        "tables": entries,
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=False)
        f.write("\n")

    elapsed = time.time() - t0
    print(f"\nWrote {args.out} -- {len(tables)} tables, "
          f"{'ALL MATCH' if not mismatches else f'{len(mismatches)} MISMATCH(ES)'}, "
          f"{elapsed:.1f}s", file=sys.stderr)
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
