#!/usr/bin/env python3
"""migrate-demo-to-pg.py -- bulk copy of the two remaining live demo-side
SQLite stores into the corporatetraveldc-pgsql Postgres instance
(pg_schema/0057_demo.sql):

  1. /var/lib/corporatetraveldc/demo.db's `snapshots` table
     (67,331 rows as of the 2026-09-20 recon) -> demo_snapshots
  2. /var/lib/corporatetraveldc-demo-state/demo_access.db's `profiles`
     table (7 rows) -> demo_profiles

Deliberately standalone, not a mode of scripts/migrate-sqlite-to-pg.py --
same reasoning as migrate-second-brain-to-pg.py's own docstring: that
script's TABLE_PLAN is asserted at import time to exactly match
common.db_backend.PG_TABLES (the dispatch write-path's 65-table set), and
these two sources are different databases entirely, never part of that
set. Style/rigor (row-count + spot-hash verification, TRUNCATE + reload
idempotency) follows both migrate-sqlite-to-pg.py and
migrate-second-brain-to-pg.py.

Explicitly OUT of scope here (see src/demo/db.py's module docstring for
the full reasoning): scripts/scrub-demo-source.py's sovereign
demo-source.db (a THIRD, separate SQLite file demo_api.py actually reads
-- confirmed live, demo_api.py's DEMO_DB never points at demo.db) and
dispatch-chat.db (owned by a separate, concurrent cutover pass -- see
pg_schema/0056_dispatch_chat.sql). Neither is touched by this script.

Column shapes are a 1:1 rename-only port (see pg_schema/0057_demo.sql's
header): every source column name equals its destination column name,
so this uses the same generic copy_table()/verify_table() shape as
migrate-second-brain-to-pg.py's RELATIONAL_TABLES path, parameterized
over (sqlite_path, sqlite_table, pg_table) instead of a single fixed
source file. compressed/auto_scale/active go from SQLite INTEGER 0/1 to
Postgres BOOLEAN -- copied as-is (no Python-side conversion): Postgres's
boolean input parser accepts '0'/'1' natively, confirmed against a
throwaway copy in this script's own --yes run (see the module-level
TEST NOTE below).

--database lets this be pointed at a scratch database instead of the
real DISPATCH_PG_DB for a dry-run/rehearsal-safe test (same override
shape as scripts/pg_migrate.py's --database and
scripts/migrate-sqlite-to-pg.py's --database), which is how this script
was written and verified: never run with --yes against the live box from
this pass -- see the "TESTED" note below and the orchestrator's own
apply instructions.

Usage:
  python3 scripts/migrate-demo-to-pg.py --list
  python3 scripts/migrate-demo-to-pg.py --dry-run
  python3 scripts/migrate-demo-to-pg.py --yes
  python3 scripts/migrate-demo-to-pg.py --verify-only
  python3 scripts/migrate-demo-to-pg.py --tables demo_profiles --yes
  python3 scripts/migrate-demo-to-pg.py --database corporatetraveldc_scratch --yes
  python3 scripts/migrate-demo-to-pg.py --demo-db /path/to/demo.db --yes
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psycopg  # noqa: E402
from common import db_backend  # noqa: E402

DEFAULT_DEMO_SQLITE_DB = "/var/lib/corporatetraveldc/demo.db"
DEFAULT_DEMO_ACCESS_SQLITE_DB = "/var/lib/corporatetraveldc-demo-state/demo_access.db"


@dataclass(frozen=True)
class Source:
    name: str            # pg table name, also the --tables selector
    sqlite_table: str
    default_sqlite_db: str


SOURCES: list[Source] = [
    Source("demo_snapshots", "snapshots", DEFAULT_DEMO_SQLITE_DB),
    Source("demo_profiles", "profiles", DEFAULT_DEMO_ACCESS_SQLITE_DB),
]


def _canon(v) -> str:
    """String form used for the spot-hash comparison. bool is normalized
    to '0'/'1' -- found live in this script's own throwaway-fixture test
    run: SQLite stores compressed/auto_scale/active as INTEGER 0/1, but
    psycopg reads the destination BOOLEAN columns back as real Python
    bool, so str(1) != str(True) produced a false-positive row-hash
    MISMATCH on data that COPY had actually copied correctly. bool is
    checked before int (bool is an int subclass in Python)."""
    if v is None:
        return "\x00"
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _row_hash(values) -> str:
    canon = "\x1f".join(_canon(v) for v in values)
    return hashlib.sha256(canon.encode("utf-8", "surrogateescape")).hexdigest()


def sqlite_columns(sconn: sqlite3.Connection, table: str) -> list[str]:
    rows = sconn.execute(f"PRAGMA table_info({table})").fetchall()
    if not rows:
        raise ValueError(f"{table}: not found in source SQLite file")
    return [r[1] for r in rows]


def sqlite_pk_columns(sconn: sqlite3.Connection, table: str) -> list[str]:
    rows = sconn.execute(f"PRAGMA table_info({table})").fetchall()
    pk = [(r[5], r[1]) for r in rows if r[5]]
    pk.sort()
    return [name for _, name in pk]


def pg_has_identity(pconn, table: str, schema: str) -> str | None:
    with pconn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s AND is_identity='YES'",
            (schema, table),
        )
        row = cur.fetchone()
        return row[0] if row else None


def copy_table(sconn, pconn, src: Source, schema: str, batch_size: int = 5000) -> dict:
    cols = sqlite_columns(sconn, src.sqlite_table)
    cols_sql = ", ".join(cols)
    scur = sconn.cursor()
    scur.execute(f"SELECT {cols_sql} FROM {src.sqlite_table}")

    with pconn.cursor() as pcur:
        pcur.execute(f"TRUNCATE TABLE {src.name}")
        n = 0
        with pcur.copy(f"COPY {src.name} ({cols_sql}) FROM STDIN") as copy:
            while True:
                batch = scur.fetchmany(batch_size)
                if not batch:
                    break
                for row in batch:
                    copy.write_row(tuple(row))
                n += len(batch)
        identity_col = pg_has_identity(pconn, src.name, schema)
        if identity_col:
            pcur.execute(
                "SELECT setval(pg_get_serial_sequence(%s, %s), "
                f"COALESCE((SELECT MAX({identity_col}) FROM {src.name}), 1))",
                (f"{schema}.{src.name}", identity_col),
            )
    pconn.commit()
    return verify_table(sconn, pconn, src)


def verify_table(sconn, pconn, src: Source) -> dict:
    cols = sqlite_columns(sconn, src.sqlite_table)
    cols_sql = ", ".join(cols)
    pk_cols = sqlite_pk_columns(sconn, src.sqlite_table) or cols[:1]
    order_sql = ", ".join(pk_cols)

    s_count = sconn.execute(f"SELECT COUNT(*) FROM {src.sqlite_table}").fetchone()[0]
    roller = hashlib.sha256()
    for row in sconn.execute(f"SELECT {cols_sql} FROM {src.sqlite_table} ORDER BY {order_sql}"):
        roller.update(_row_hash(row).encode("ascii"))
    s_hash = roller.hexdigest()

    with pconn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {src.name}")
        p_count = cur.fetchone()[0]
        proller = hashlib.sha256()
        cur.execute(f"SELECT {cols_sql} FROM {src.name} ORDER BY {order_sql}")
        for row in cur:
            proller.update(_row_hash(row).encode("ascii"))
        p_hash = proller.hexdigest()

    return {
        "table": f"{src.sqlite_table} -> {src.name}",
        "sqlite_count": s_count, "pg_count": p_count,
        "row_count_ok": s_count == p_count, "spot_hash_ok": s_hash == p_hash,
    }


def _open_sqlite(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        raise SystemExit(f"migrate-demo-to-pg.py: source file not found: {path}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _pg_conninfo_for(database: str | None) -> str:
    conninfo = db_backend._pg_conninfo()  # noqa: SLF001 -- same reuse pg_migrate.py makes
    if database:
        parts = [p for p in conninfo.split(" ") if not p.startswith("dbname=")]
        parts.append(f"dbname={database}")
        conninfo = " ".join(parts)
    return conninfo


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--tables", help="Comma-separated subset of table names (demo_snapshots,demo_profiles)")
    ap.add_argument("--database", help="Postgres database to target instead of DISPATCH_PG_DB (scratch/rehearsal testing)")
    ap.add_argument("--schema", default="public")
    ap.add_argument("--demo-db", default=DEFAULT_DEMO_SQLITE_DB, help="Path to demo.db (source for demo_snapshots)")
    ap.add_argument("--demo-access-db", default=DEFAULT_DEMO_ACCESS_SQLITE_DB, help="Path to demo_access.db (source for demo_profiles)")
    args = ap.parse_args()

    sqlite_paths = {"demo_snapshots": args.demo_db, "demo_profiles": args.demo_access_db}

    selected = SOURCES
    if args.tables:
        wanted = {t.strip() for t in args.tables.split(",") if t.strip()}
        unknown = wanted - {s.name for s in SOURCES}
        if unknown:
            raise SystemExit(f"migrate-demo-to-pg.py: unknown table(s) {sorted(unknown)}")
        selected = [s for s in SOURCES if s.name in wanted]

    if args.list:
        for s in selected:
            print(f"{s.sqlite_table} ({sqlite_paths[s.name]}) -> {s.name}")
        return 0

    sconns = {s.name: _open_sqlite(sqlite_paths[s.name]) for s in selected}
    pconn = psycopg.connect(_pg_conninfo_for(args.database), autocommit=False)

    # 2026-09-21: bulk COPY needs a per-session statement_timeout. The
    # server-wide default is 60s (config/postgresql.conf), sized for the
    # sub-second dispatch write path -- but copy_table() holds ONE `COPY
    # ... FROM STDIN` statement open for an entire table (the fetchmany
    # batching below is only on the SQLite read side, it does not close and
    # reopen the COPY), so the whole 67k-row demo_snapshots load has to fit
    # inside that one 60s budget. It cannot, on this hardware: two real runs
    # died at row 12112 and row 9115 -- FEWER rows on the second attempt
    # despite lower system load, which is the signature of a fixed
    # wall-clock cap rather than contention.
    #
    # Deliberately bounded, NOT `statement_timeout = 0`. Unbounded is what
    # caused the 2026-09-20 seven-hour outage (an ad-hoc snapshot script left
    # running unsupervised with no cap -- see CLAUDE.md); the correction
    # adopted then was a bounded wall-clock ceiling, and this follows it.
    # 15min is ~15x the observed need with headroom, and still terminates on
    # its own if something pathological happens. Session-scoped, so the 60s
    # protection stays in force for every other connection.
    #
    # This is exactly the pattern postgresql.conf's own comment prescribes:
    # "report-tier skills that need longer set it per-session."
    with pconn.cursor() as _cur:
        # 30min, not 15: the first successful run (2026-09-21) took 1098s
        # end-to-end for 67,751 rows, and the single COPY statement inside
        # that cleared the 15min ceiling by seconds, not minutes. The table
        # grows continuously (the demo recorder appends every 5min), so 15min
        # was already a latent failure for the next rerun. Still bounded --
        # the point is headroom, not removing the guard.
        #
        # STANDING ORDER (operator, 2026-09-21): for each ~2-week period of
        # accumulated new content that a full-archive re-ingest has to carry,
        # raise this by a further 15min -- UNLESS a measured run comes back
        # faster, or the script is changed to copy only the delta. Record the
        # observed runtime here each time it is raised, so the decision stays
        # evidence-based rather than ratcheting on assumption:
        #   2026-09-21  67,751 rows  1098s total  -> 30min
        #
        # Worth stating plainly, because the ratchet hides it: this is linear
        # growth against a bounded ceiling and it compounds -- six months of
        # +15min/2wk is a >3h timeout, at which point a failure mid-run costs
        # three hours to discover. The durable fix is the delta copy the
        # operator already named (COPY only rows newer than the max
        # already-migrated id/timestamp, instead of TRUNCATE + full reload),
        # which makes runtime proportional to new content rather than to
        # total archive size. Treat the ratchet as a stopgap with a shelf
        # life, not a strategy.
        _cur.execute("SET statement_timeout = '30min'")
        # Second timeout, found the hard way once the first was fixed: this
        # connection is autocommit=False, so it holds an open transaction for
        # the whole run. Between tables the script verifies the table it just
        # copied -- counting rows and spot-hashing against SQLITE -- during
        # which the Postgres side is idle-in-transaction. The server default
        # is 60s (config/postgresql.conf), sized to stop a runaway report
        # holding locks; a multi-minute SQLite verification blows straight
        # through it and the connection is terminated at the next PG
        # statement (observed: TRUNCATE of the second table, immediately
        # after the first table copied successfully).
        # Bounded for the same reason as above -- never 0.
        _cur.execute("SET idle_in_transaction_session_timeout = '15min'")

    if args.dry_run:
        for s in selected:
            n = sconns[s.name].execute(f"SELECT COUNT(*) FROM {s.sqlite_table}").fetchone()[0]
            print(f"  {s.sqlite_table + ' -> ' + s.name:32s} {n:>8} row(s) to copy")
        return 0

    if args.verify_only:
        failed = False
        for s in selected:
            r = verify_table(sconns[s.name], pconn, s)
            flag = "OK" if (r["row_count_ok"] and r["spot_hash_ok"]) else "MISMATCH"
            print(f"{r['table']:32s} sqlite={r['sqlite_count']:>8} pg={r['pg_count']:>8}  {flag}")
            failed = failed or flag == "MISMATCH"
        return 1 if failed else 0

    if not args.yes:
        print("Pass --yes to actually copy (or --dry-run / --verify-only / --list).", file=sys.stderr)
        return 2

    t0 = time.time()
    failed = False
    for s in selected:
        r = copy_table(sconns[s.name], pconn, s, args.schema)
        flag = "OK" if (r["row_count_ok"] and r["spot_hash_ok"]) else "MISMATCH"
        print(f"{r['table']:32s} sqlite={r['sqlite_count']:>8} pg={r['pg_count']:>8}  {flag}")
        failed = failed or flag == "MISMATCH"
    print(f"\nDone in {time.time()-t0:.1f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
