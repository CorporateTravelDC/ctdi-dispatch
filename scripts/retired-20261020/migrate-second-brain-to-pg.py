#!/usr/bin/env python3
"""migrate-second-brain-to-pg.py -- bulk copy of the second-brain vault
index (/var/lib/corporatetraveldc/second_brain_index.db, SQLite) into the
corporatetraveldc-pgsql Postgres instance (pg_schema/0055).

Deliberately standalone, not a mode of scripts/migrate-sqlite-to-pg.py --
that script's TABLE_PLAN is asserted at import time to exactly match
common.db_backend.PG_TABLES (the dispatch write-path table set), and the
second-brain tables are a different source DB and a different table set
entirely (docs/POSTGRES_MIGRATION.md sec2's JOIN-exception reversal covers
both moves for the same reason, but they are two independent copies).

Two table groups, copied in this order (vault_notes_fulltext has an FK to
vault_documents.path, so vault_documents must land first):

  1. The 14 relational/semantic tables -- straight full-table copy, same
     COPY-protocol/TRUNCATE-CASCADE/spot-hash pattern as
     migrate-sqlite-to-pg.py's copy_table()/verify_table().
  2. vault_notes_fulltext -- bespoke: source is vault_notes_fts, a SQLite
     FTS5 virtual table (path/title/content/tags, porter tokenizer), not a
     plain table. Reads path/title/content/tags and inserts them;
     search_vector is `GENERATED ALWAYS AS (...) STORED` in Postgres and
     is never written directly -- Postgres computes it from the inserted
     columns.

Idempotent: each table TRUNCATEd (CASCADE for vault_documents, since
vault_notes_fulltext FKs to it) and reloaded, so a re-run always leaves
Postgres an exact mirror of second_brain_index.db at copy time.

Usage:
  python3 scripts/migrate-second-brain-to-pg.py --list
  python3 scripts/migrate-second-brain-to-pg.py --dry-run
  python3 scripts/migrate-second-brain-to-pg.py --yes
  python3 scripts/migrate-second-brain-to-pg.py --verify-only
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("NEXTCLOUD_ADMIN_USER", "corporatetraveldc")

import psycopg  # noqa: E402
from common import db_backend  # noqa: E402

SQLITE_DB = "/var/lib/corporatetraveldc/second_brain_index.db"

# Plain relational tables: source table name == destination table name,
# columns copied 1:1 in SQLite column order (schema was hand-mirrored in
# pg_schema/0055_second_brain_index.sql, so this holds for all 14).
RELATIONAL_TABLES = [
    "vault_documents",  # parent of vault_notes_fulltext's FK -- must be first
    "entities",
    "vault_links",
    "semantic_note_chronology",
    "semantic_meta",
    "semantic_facets",
    "semantic_concepts",
    "semantic_labels",
    "semantic_relations",
    "semantic_agents",
    "semantic_metrics",
    "semantic_note_concepts",
    "semantic_unmapped_tags",
    "semantic_note_derivations",
]

FTS_TABLE = "vault_notes_fts"       # SQLite FTS5 source
FTS_DEST = "vault_notes_fulltext"   # Postgres destination
FTS_COLS = ["path", "title", "content", "tags"]  # search_vector excluded: GENERATED


def _row_hash(values) -> str:
    canon = "\x1f".join("\x00" if v is None else str(v) for v in values)
    return hashlib.sha256(canon.encode("utf-8", "surrogateescape")).hexdigest()


def sqlite_columns(sconn: sqlite3.Connection, table: str) -> list[str]:
    rows = sconn.execute(f"PRAGMA table_info({table})").fetchall()
    if not rows:
        raise ValueError(f"{table}: not found in {SQLITE_DB}")
    return [r[1] for r in rows]


def sqlite_pk_columns(sconn: sqlite3.Connection, table: str) -> list[str]:
    rows = sconn.execute(f"PRAGMA table_info({table})").fetchall()
    pk = [(r[5], r[1]) for r in rows if r[5]]
    pk.sort()
    return [name for _, name in pk]


def pg_has_identity(pconn, table: str) -> str | None:
    with pconn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND is_identity='YES'",
            (table,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def copy_relational_table(sconn, pconn, table: str, batch_size: int = 5000) -> dict:
    cols = sqlite_columns(sconn, table)
    cols_sql = ", ".join(cols)
    scur = sconn.cursor()
    scur.execute(f"SELECT {cols_sql} FROM {table}")

    with pconn.cursor() as pcur:
        pcur.execute(f"TRUNCATE TABLE {table} CASCADE")
        n = 0
        with pcur.copy(f"COPY {table} ({cols_sql}) FROM STDIN") as copy:
            while True:
                batch = scur.fetchmany(batch_size)
                if not batch:
                    break
                for row in batch:
                    copy.write_row(tuple(row))
                n += len(batch)
        identity_col = pg_has_identity(pconn, table)
        if identity_col:
            pcur.execute(
                "SELECT setval(pg_get_serial_sequence(%s, %s), "
                f"COALESCE((SELECT MAX({identity_col}) FROM {table}), 1))",
                (table, identity_col),
            )
    pconn.commit()
    return verify_relational_table(sconn, pconn, table)


def verify_relational_table(sconn, pconn, table: str) -> dict:
    cols = sqlite_columns(sconn, table)
    cols_sql = ", ".join(cols)
    pk_cols = sqlite_pk_columns(sconn, table) or cols[:1]
    order_sql = ", ".join(pk_cols)

    s_count = sconn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    roller = hashlib.sha256()
    for row in sconn.execute(f"SELECT {cols_sql} FROM {table} ORDER BY {order_sql}"):
        roller.update(_row_hash(row).encode("ascii"))
    s_hash = roller.hexdigest()

    with pconn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        p_count = cur.fetchone()[0]
        proller = hashlib.sha256()
        cur.execute(f"SELECT {cols_sql} FROM {table} ORDER BY {order_sql}")
        for row in cur:
            proller.update(_row_hash(row).encode("ascii"))
        p_hash = proller.hexdigest()

    return {
        "table": table, "sqlite_count": s_count, "pg_count": p_count,
        "row_count_ok": s_count == p_count, "spot_hash_ok": s_hash == p_hash,
    }


def copy_fts(sconn, pconn, batch_size: int = 2000) -> dict:
    cols_sql = ", ".join(FTS_COLS)
    scur = sconn.cursor()
    scur.execute(f"SELECT {cols_sql} FROM {FTS_TABLE} ORDER BY path")

    with pconn.cursor() as pcur:
        pcur.execute(f"TRUNCATE TABLE {FTS_DEST} CASCADE")
        n = 0
        with pcur.copy(f"COPY {FTS_DEST} ({cols_sql}) FROM STDIN") as copy:
            while True:
                batch = scur.fetchmany(batch_size)
                if not batch:
                    break
                for row in batch:
                    copy.write_row(tuple(row))
                n += len(batch)
    pconn.commit()
    return verify_fts(sconn, pconn)


def verify_fts(sconn, pconn) -> dict:
    s_count = sconn.execute(f"SELECT COUNT(*) FROM {FTS_TABLE}").fetchone()[0]
    with pconn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {FTS_DEST}")
        p_count = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM {FTS_DEST} WHERE search_vector IS NOT NULL")
        p_vec_count = cur.fetchone()[0]
    return {
        "table": f"{FTS_TABLE} -> {FTS_DEST}",
        "sqlite_count": s_count, "pg_count": p_count,
        "row_count_ok": s_count == p_count,
        "tsvector_populated": p_vec_count, "tsvector_ok": p_vec_count == p_count,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    all_tables = RELATIONAL_TABLES + [f"{FTS_TABLE}->{FTS_DEST}"]
    if args.list:
        for t in all_tables:
            print(t)
        return 0

    sconn = sqlite3.connect(f"file:{SQLITE_DB}?mode=ro", uri=True)
    pconn = psycopg.connect(db_backend._pg_conninfo(), autocommit=False)  # noqa: SLF001

    if args.dry_run:
        for t in RELATIONAL_TABLES:
            n = sconn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:32s} {n:>8} row(s) to copy")
        n = sconn.execute(f"SELECT COUNT(*) FROM {FTS_TABLE}").fetchone()[0]
        print(f"  {FTS_TABLE + ' -> ' + FTS_DEST:32s} {n:>8} row(s) to copy")
        return 0

    if args.verify_only:
        failed = False
        for t in RELATIONAL_TABLES:
            r = verify_relational_table(sconn, pconn, t)
            flag = "OK" if (r["row_count_ok"] and r["spot_hash_ok"]) else "MISMATCH"
            print(f"{r['table']:32s} sqlite={r['sqlite_count']:>8} pg={r['pg_count']:>8}  {flag}")
            failed = failed or flag == "MISMATCH"
        r = verify_fts(sconn, pconn)
        flag = "OK" if (r["row_count_ok"] and r["tsvector_ok"]) else "MISMATCH"
        print(f"{r['table']:32s} sqlite={r['sqlite_count']:>8} pg={r['pg_count']:>8} "
              f"tsvector_populated={r['tsvector_populated']:>8}  {flag}")
        failed = failed or flag == "MISMATCH"
        return 1 if failed else 0

    if not args.yes:
        print("Pass --yes to actually copy (or --dry-run / --verify-only / --list).", file=sys.stderr)
        return 2

    t0 = time.time()
    for t in RELATIONAL_TABLES:
        r = copy_relational_table(sconn, pconn, t)
        flag = "OK" if (r["row_count_ok"] and r["spot_hash_ok"]) else "MISMATCH"
        print(f"{r['table']:32s} sqlite={r['sqlite_count']:>8} pg={r['pg_count']:>8}  {flag}")
    r = copy_fts(sconn, pconn)
    flag = "OK" if (r["row_count_ok"] and r["tsvector_ok"]) else "MISMATCH"
    print(f"{r['table']:32s} sqlite={r['sqlite_count']:>8} pg={r['pg_count']:>8} "
          f"tsvector_populated={r['tsvector_populated']:>8}  {flag}")
    print(f"\nDone in {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
