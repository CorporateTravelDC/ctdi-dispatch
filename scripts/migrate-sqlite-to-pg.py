#!/usr/bin/env python3
"""Per-table copy of the 65 PG-bound tables from SQLite into Postgres.

Phase 2 of docs/POSTGRES_MIGRATION.md §5. This is the REHEARSAL/COPY tool,
run repeatedly while every service stays on DISPATCH_DB_BACKEND=sqlite (see
§5's cutover sequence in Phase 3, which is NOT this script -- this script
never touches DISPATCH_DB_BACKEND and never stops/starts any service).

SQLite is opened read-only (`mode=ro` URI) -- this script cannot write to
it even by accident; it is the migration source, not a target. Postgres is
the only thing written.

Idempotent: each table is TRUNCATEd and reloaded inside one transaction, so
a re-run (rehearsal, retry after a failure, nightly cron once this graduates
past rehearsal) always leaves Postgres as an exact mirror of what SQLite
held at copy time -- never an accumulating delta, never a duplicate.

Per-table classification (TIME_SERIES vs STATE) is in TABLE_PLAN below.
See its module docstring for how each table was classified and from what
evidence (Phase 4's explicit partition list, an existing age-based
`prune_*()` function in src/common/db.py, or the table's own INSERT
dialect -- ON CONFLICT DO UPDATE upsert vs. plain/OR IGNORE insert-only).
Run --list to print the plan; run --audit-only to sanity-check schema drift
and numeric-column content before ever copying a row.

Usage:
    scripts/migrate-sqlite-to-pg.py --list
    scripts/migrate-sqlite-to-pg.py --audit-only
    scripts/migrate-sqlite-to-pg.py --dry-run
    scripts/migrate-sqlite-to-pg.py --tables feed_state,acars_messages
    scripts/migrate-sqlite-to-pg.py --tables flight_events --window-days 90
    scripts/migrate-sqlite-to-pg.py                      # all 65, default 90-day window
    scripts/migrate-sqlite-to-pg.py --database corporatetraveldc_pgphase1_test  # scratch DB
    scripts/migrate-sqlite-to-pg.py --verify-only --tables feed_state   # re-check without copying

See --help for the rest (--batch-size, --schema, --yes, --sqlite-path).
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

from common import config, db_backend  # noqa: E402

DEFAULT_WINDOW_DAYS = 90
DEFAULT_BATCH_SIZE = 5000
# How many rows (evenly spaced across the copied set, ordered by PK) get
# individually hashed for the post-copy spot-hash check. Small enough to
# stay fast even on the 1.19M-row flight_events table; large enough that a
# systematically wrong column mapping or type coercion is very unlikely to
# hide between sampled rows (evenly spaced, not clustered at one end).
SPOT_HASH_SAMPLE = 200

# SQLite storage-class names that a Postgres numeric column (int/bigint/
# real/double precision/numeric/smallint) can accept. Anything else
# (SQLite 'text', 'blob') sitting in a column PG has typed numeric is
# exactly the corruption docs/POSTGRES_MIGRATION.md §4 warns about ("PG
# rejects text in numeric columns") -- SQLite's per-VALUE dynamic typing
# means a column declared REAL can still hold a TEXT value if something
# ever wrote one, and PG's COPY will hard-reject it (best case) or, if the
# value happens to parse (e.g. "123"), silently coerce it (worst case, and
# exactly the silent-corruption path we must not take). The audit runs
# BEFORE any COPY and refuses to copy a table where this is found.
_PG_NUMERIC_TYPES = {
    "smallint", "integer", "bigint", "real", "double precision", "numeric",
}


# ═══════════════════════════════════════════════════════════════════════════
# Table plan -- TIME_SERIES (90-day window) vs STATE (full copy)
# ═══════════════════════════════════════════════════════════════════════════
#
# docs/POSTGRES_MIGRATION.md §5: "Time-series tables get a 90-day window;
# state tables get a full copy." The doc does not enumerate which of the
# 65 tables are which (§6's Phase-4 partition list -- flight_events,
# train_events, vessel_events, *_history, tbfm_sequences,
# surface_movement_events, acars_messages, "..." -- is itself illustrative,
# not exhaustive: it names the worst offenders for partitioning, not a
# complete Phase-2 classification). Verified 2026-09-18 against the actual
# codebase rather than guessed, using three kinds of evidence, strongest
# first:
#
#   1. Named explicitly in §6's Phase-4 partition list, or matches its
#      "*_history" glob.
#   2. Has an existing age-based `prune_*(days=N)` function in
#      src/common/db.py (grep: "def prune\b" / "def.*_prune_" /
#      "def cleanup_expired_notams") -- direct evidence the codebase
#      already treats the table as bounded-retention.
#   3. INSERT dialect at the actual call site (grep verified against
#      src/common/db.py + src/common/db_swim.py 2026-09-18): a table
#      that is only ever a plain `INSERT INTO` or `INSERT OR IGNORE INTO`
#      (no `ON CONFLICT ... DO UPDATE`) can only grow -- there is no
#      code path that overwrites an existing row, so without a window
#      every copy re-reads the table's entire, ever-growing history.
#      Conversely a table upserted via `ON CONFLICT (...) DO UPDATE` on a
#      genuinely long-lived entity key (a feed name, an airport, a token
#      hash, a session id) has a naturally bounded row count and is a
#      STATE table regardless of size.
#
#      Three tables break the naive "upsert = bounded" reading of rule 3
#      -- they ARE upserted via ON CONFLICT, but the conflict key itself
#      is effectively a per-occurrence identifier (a specific flight
#      instance's GUFI, a specific track's transient track_id, a specific
#      TFMS plan-removal's callsign+IGTD+city-pair) rather than a
#      long-lived entity, so the table still grows without bound even
#      though any single row can be updated in place before it ages out:
#      flight_events, tbfm_sequences, surface_movement_events,
#      tfms_plan_removals, flight_ooooi_times. All five are confirmed by
#      an existing prune_*()/export-and-delete function (rule 2) or, for
#      tbfm_sequences/surface_movement_events, by the explicit Phase-4
#      naming (rule 1) -- this is not a guess layered on top of rule 3,
#      it is rule 3's apparent STATE reading being overridden by stronger
#      evidence from rules 1/2.
#
# ts_type: "epoch" = REAL unix seconds (compare against a float cutoff);
# "iso" = TEXT ISO-8601 (compare lexicographically -- valid for this
# codebase's ISO-8601 strings, per db_backend.py's own translate_sql()
# docstring and Appendix A's "TEXT ISO-8601 (lexicographic compare)" note).


@dataclass(frozen=True)
class TablePlan:
    kind: str  # "time_series" | "state"
    ts_col: Optional[str] = None
    ts_type: Optional[str] = None  # "epoch" | "iso"
    evidence: str = ""


def _ts(col: str, ts_type: str, evidence: str) -> TablePlan:
    return TablePlan(kind="time_series", ts_col=col, ts_type=ts_type, evidence=evidence)


def _state(evidence: str) -> TablePlan:
    return TablePlan(kind="state", evidence=evidence)


TABLE_PLAN: dict[str, TablePlan] = {
    # ── time_series (90-day window) ─────────────────────────────────────
    "acars_messages": _ts("received_at", "iso", "Phase4-named; insert-only"),
    "amtrak_status": _ts("fetched_at", "epoch", "insert-only poll-snapshot log"),
    "audit_log": _ts("event_time", "epoch", "prune_audit_log(days=90)"),
    "board_messages": _ts("ts", "iso", "prune_board_messages(days=180)"),
    "brief_archive": _ts("generated_at", "iso", "insert-only log"),
    "convective_sigmet_archive": _ts("first_seen", "iso", "INSERT OR IGNORE, insert-only"),
    "cps_scores": _ts("computed_at", "epoch", "insert-only log"),
    "fdps_destination_changes": _ts("detected_at", "iso", "insert-only log"),
    "fdps_diversion_continuations": _ts("detected_at", "iso", "INSERT OR IGNORE, insert-only"),
    "fdps_route_versions": _ts("first_seen", "iso", "insert-only append-versioning log, 259k rows"),
    "flight_events": _ts("updated_at", "epoch", "Phase4-named; export_old_flight_events()/delete_flight_events_by_id() age-based archival"),
    "flight_ooooi_times": _ts("updated_at", "epoch", "prune_flight_ooooi_times(days=90); upsert on transient gufi key"),
    "hot_alerts": _ts("computed_at", "epoch", "insert-only log"),
    "international_aviation_feed": _ts("fetched_at", "epoch", "prune_international_aviation_feed(days=30)"),
    "local_airspace_alerts": _ts("fired_at", "iso", "prune_local_airspace_alerts(days=90)"),
    "metar_history": _ts("recorded_at", "epoch", "*_history glob; insert-only"),
    "osint_items": _ts("ingested_at", "epoch", "osint_prune_items(max_age_days=30); INSERT OR IGNORE"),
    "stdds_rvr_history": _ts("recorded_at", "iso", "*_history glob; insert-only"),
    "stdds_safety_status_history": _ts("changed_at", "iso", "*_history glob; prune_stdds_safety_status_history(days=180)"),
    "surface_movement_events": _ts("event_time", "iso", "Phase4-named; upsert on transient track_id key, 155k rows"),
    "tbfm_sequences": _ts("last_seen", "iso", "Phase4-named; upsert on transient flight_id key, 50k rows"),
    "tdes_departure_events": _ts("event_time", "iso", "INSERT OR IGNORE, insert-only"),
    "tdls_messages": _ts("received_at", "iso", "insert-only log, 15k rows"),
    "tfms_plan_removals": _ts("detected_at", "iso", "upsert on transient callsign+igtd+city-pair key, 120k rows"),
    "train_events": _ts("fetched_at", "epoch", "Phase4-named; prune_train_events(days=30, raised to 90 at cutover per §6)"),
    "vessel_events": _ts("fetched_at", "epoch", "Phase4-named; insert-only"),
    "watchlist_history": _ts("fired_at", "iso", "insert-only log"),
    "webhook_events": _ts("received_at", "epoch", "prune_webhook_events(days=90)"),
    "wpc_discussions": _ts("issued_at", "epoch", "insert-only log (existing prune is keep-per-product, not age)"),

    # ── state (full copy) ───────────────────────────────────────────────
    "approval_requests": _state("workflow record, plain insert, id PK"),
    "atcscc_opsplan": _state("ON CONFLICT(plan_date) DO UPDATE -- one row/day, upserted through the day"),
    "auth_tokens": _state("ON CONFLICT-free but token_hash is a durable entity key; small (~dozens)"),
    "bandwidth_priority_state": _state("singleton row (id=1 CHECK)"),
    "board_enroll_nonces": _state("small, grace-pruned by prune_expired_board_auth()"),
    "board_presence": _state("singleton row (id=1 CHECK)"),
    "board_refresh_grace": _state("INSERT OR REPLACE on old_token_hash PK"),
    "board_tokens": _state("token_hash PK, grace-pruned by prune_expired_board_auth()"),
    "datis_snapshots": _state("ON CONFLICT(airport) -- current D-ATIS text per airport"),
    "feed_data_usage": _state("ON CONFLICT(feed_name) -- current usage counters per feed"),
    "feed_state": _state("ON CONFLICT(feed_name) -- current poll state per feed"),
    "itws_alerts": _state("ON CONFLICT(airport, product_type) -- current alert per airport/product"),
    "local_aircraft": _state("ON CONFLICT(icao_hex) -- current position per aircraft"),
    "metar_snapshot": _state("ON CONFLICT(station) -- current METAR per station"),
    "nas_programs": _state("ON CONFLICT(program_id) -- current program state"),
    "notams": _state("ON CONFLICT(notam_id); cleanup_expired_notams() by effective_end"),
    "nws_alerts": _state("ON CONFLICT(alert_id); swept by not-in-current-fetch"),
    "nws_forecast": _state("PK zone, no growth"),
    "ops_plan": _state("plan-of-record, small (0 rows live)"),
    "osint_scopes": _state("scope config, small"),
    "pull_path_status": _state("ON CONFLICT(feed_name) -- current pull-path health per feed"),
    "runsheet": _state("plain insert but §2's own bucketing + no existing prune; business record, kept in full -- flag for operator review of retention intent before Phase 4"),
    "session_grants": _state("SR1/SR2 grant record; prune_expired_session_grants() removes expired, not aged-out"),
    "stdds_rvr": _state("ON CONFLICT(airport, runway) -- current RVR per runway"),
    "stdds_safety_status": _state("PK airport -- current safety-status bitmask per airport"),
    "surface_tracks": _state("ON CONFLICT(airport, track_id) -- current surface position, bounded live-track count"),
    "swim_alerts": _state("PK alert_type -- current alert per type"),
    "terminal_tracks": _state("ON CONFLICT(facility, track_id) -- current terminal position, bounded live-track count"),
    "tfms_edct_slots": _state("ON CONFLICT(control_element, aircraft_id) -- current EDCT slot"),
    "tfms_param_delay_stats": _state("ON CONFLICT(elem_name, parameters_type, tmi_state) -- current stats snapshot"),
    "tfms_reroutes": _state("ON CONFLICT(reroute_id) -- current reroute-plan state"),
    "tfrs": _state("ON CONFLICT(tfr_id); swept by not-in-current-fetch"),
    "trigger_log": _state("admin mutation queue, small"),
    "ustrains_departures": _state("ON CONFLICT(train_id, station_id) -- current departure board (0 rows live)"),
    "watchlist_entries": _state("ON CONFLICT(id) -- the watchlist itself, bounded by active watches"),
    "watchlist_sessions": _state("plain insert but read via status='active' in get_protected_flight_ids() -- MUST stay a full copy: windowing could drop an old-but-still-active session and silently un-protect its flight_events rows, exactly the cross-engine transaction §5/Appendix A calls out"),

    # ── former reference tables, moved 2026-09-19 (docs/POSTGRES_MIGRATION.md
    # §2's JOIN exception -- see db_backend.py's REFERENCE_TABLES comment) --
    # all bulk-loaded/rebuilt wholesale by a batch loader, never upserted
    # row-by-row; full copy every run, same as any other small state table.
    "cifp_fixes": _state("bulk-loaded from AIRAC cycle data, full replace each cycle"),
    "cifp_holds": _state("bulk-loaded from AIRAC cycle data, full replace each cycle"),
    "cifp_procedure_legs": _state("bulk-loaded from AIRAC cycle data, full replace each cycle"),
    "cifp_meta": _state("singleton-ish cycle metadata, 2 rows"),
    "faa_aircraft_registry": _state("bulk-loaded from FAA registry pull, ON CONFLICT(n_number) DO UPDATE"),
    "faa_aircraft_reference": _state("bulk-loaded from FAA registry pull, ON CONFLICT(code) DO UPDATE"),
    "faa_registry_meta": _state("singleton-ish pull metadata, 1 row"),
    "faa_ladd_aircraft": _state("bulk-loaded LADD opt-out list, ON CONFLICT(n_number) DO UPDATE"),
    "opensky_aircraft_registry": _state("bulk-loaded from OpenSky registry pull, ON CONFLICT(icao24) DO UPDATE"),
    "opensky_registry_meta": _state("singleton-ish pull metadata, 3 rows"),
    "codeshare_map": _state("small, operator/derived codeshare mapping, plain insert"),
}


def _assert_plan_matches_authoritative_list() -> None:
    plan_tables = set(TABLE_PLAN)
    authoritative = db_backend.PG_TABLES
    missing = authoritative - plan_tables
    extra = plan_tables - authoritative
    if missing or extra:
        raise SystemExit(
            "migrate-sqlite-to-pg.py: TABLE_PLAN has drifted from "
            "common.db_backend.PG_TABLES (the authoritative list verified "
            "against Phase 1's actual migrations) -- "
            f"missing from plan: {sorted(missing)}, "
            f"not in PG_TABLES: {sorted(extra)}. Fix TABLE_PLAN before "
            "running anything else."
        )
    overlap = plan_tables & db_backend.REFERENCE_TABLES
    if overlap:
        raise SystemExit(
            f"migrate-sqlite-to-pg.py: TABLE_PLAN includes reference "
            f"table(s) {sorted(overlap)} -- reference tables stay SQLite-"
            "only (docs/POSTGRES_MIGRATION.md §2) and must never appear "
            "here."
        )


_assert_plan_matches_authoritative_list()


# ═══════════════════════════════════════════════════════════════════════════
# Schema introspection
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ColumnInfo:
    name: str
    pg_type: str
    is_identity: bool


def sqlite_columns(sconn: sqlite3.Connection, table: str) -> list[str]:
    rows = sconn.execute(f"PRAGMA table_info({table})").fetchall()
    if not rows:
        raise ValueError(f"{table}: not found in SQLite (PRAGMA table_info returned nothing)")
    return [r[1] for r in rows]  # r[1] == column name


def sqlite_pk_columns(sconn: sqlite3.Connection, table: str) -> list[str]:
    rows = sconn.execute(f"PRAGMA table_info({table})").fetchall()
    # PRAGMA table_info's 6th field (pk) is the 1-based position in the PK,
    # 0 if not part of it -- sort by that to get correct multi-column order.
    pk = [(r[5], r[1]) for r in rows if r[5]]
    pk.sort()
    return [name for _, name in pk]


def pg_columns(pconn, table: str) -> list[ColumnInfo]:
    with pconn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_identity
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (_CURRENT_SCHEMA, table),
        )
        rows = cur.fetchall()
    if not rows:
        raise ValueError(f"{table}: not found in Postgres schema {_CURRENT_SCHEMA!r}")
    return [ColumnInfo(name=r[0], pg_type=r[1], is_identity=(r[2] == "YES")) for r in rows]


_CURRENT_SCHEMA = "public"  # overwritten by main() from --schema


@dataclass
class Reconciled:
    columns: list[str]           # common columns, PG ordinal order
    sqlite_only: list[str]       # dropped -- present in SQLite, not in PG schema
    pg_only: list[str]           # PG columns with no source data (NULL/default on copy)
    numeric_columns: list[str]   # subset of `columns` that PG types as numeric
    identity_column: Optional[str]


def reconcile_columns(sconn: sqlite3.Connection, pconn, table: str) -> Reconciled:
    s_cols = set(sqlite_columns(sconn, table))
    p_cols = pg_columns(pconn, table)
    p_names = [c.name for c in p_cols]
    common = [c for c in p_names if c in s_cols]
    numeric = [c.name for c in p_cols if c.name in common and c.pg_type in _PG_NUMERIC_TYPES]
    identity = next((c.name for c in p_cols if c.is_identity), None)
    return Reconciled(
        columns=common,
        sqlite_only=sorted(s_cols - set(p_names)),
        pg_only=sorted(set(p_names) - s_cols),
        numeric_columns=numeric,
        identity_column=identity,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Pre-copy audit
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class AuditResult:
    table: str
    ok: bool
    schema_warnings: list[str] = field(default_factory=list)
    numeric_violations: list[str] = field(default_factory=list)


def _window_where(plan: TablePlan, window_days: int) -> tuple[str, list[Any]]:
    """WHERE clause + params selecting the rows this run will copy, or
    ("", []) for a full-table (state) copy."""
    if plan.kind != "time_series":
        return "", []
    cutoff_ts = time.time() - window_days * 86400
    if plan.ts_type == "epoch":
        return f"WHERE {plan.ts_col} >= ?", [cutoff_ts]
    # iso: lexicographic compare against a UTC ISO-8601 cutoff string,
    # matching how this codebase's own SWIM-table queries already compare
    # TEXT timestamps (db_backend.py module docstring / Appendix A).
    cutoff_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff_ts))
    return f"WHERE {plan.ts_col} >= ?", [cutoff_iso]


def audit_table(
    sconn: sqlite3.Connection, pconn, table: str, plan: TablePlan, window_days: int
) -> tuple[AuditResult, Reconciled]:
    rec = reconcile_columns(sconn, pconn, table)
    # 2026-09-18 fix: pconn is autocommit=False and reused across all 65
    # tables in one long-lived main-loop connection. Without this commit,
    # the implicit transaction opened by reconcile_columns()'s PG query
    # stays open while the (slow, unindexed -- see the numeric-audit loop
    # below) SQLite-side work for THIS table runs, and the connection sits
    # idle-in-transaction the whole time. Once that idle gap crosses
    # postgresql.conf's deliberate idle_in_transaction_session_timeout=60s
    # guardrail, PG kills the connection -- confirmed live: a 40-minute
    # audit-only run died on exactly this
    # (psycopg.errors.IdleInTransactionSessionTimeout) partway through.
    # Committing here closes the transaction immediately, so the
    # connection is idle-NOT-in-transaction (not guarded) during the slow
    # part, which is the actual right fix -- not raising the timeout.
    pconn.commit()
    result = AuditResult(table=table, ok=True)

    if rec.sqlite_only:
        result.schema_warnings.append(
            f"columns present in SQLite but not in the PG schema (will be "
            f"DROPPED on copy, not corrupted -- but check whether the PG "
            f"schema is missing a migration, same shape as the 0049 "
            f"auth_tokens.department drift): {rec.sqlite_only}"
        )
    if rec.pg_only:
        result.schema_warnings.append(
            f"PG columns with no SQLite source (left NULL/default on "
            f"copy): {rec.pg_only}"
        )

    where, params = _window_where(plan, window_days)
    for col in rec.numeric_columns:
        query = (
            f"SELECT COUNT(*), GROUP_CONCAT(DISTINCT typeof({col})) "
            f"FROM {table} {where} "
            f"{'AND' if where else 'WHERE'} {col} IS NOT NULL "
            f"AND typeof({col}) NOT IN ('integer','real')"
        )
        bad_count, bad_types = sconn.execute(query, params).fetchone()
        if bad_count:
            samples = sconn.execute(
                f"SELECT {col} FROM {table} {where} "
                f"{'AND' if where else 'WHERE'} {col} IS NOT NULL "
                f"AND typeof({col}) NOT IN ('integer','real') LIMIT 5",
                params,
            ).fetchall()
            result.numeric_violations.append(
                f"{col}: {bad_count} row(s) with non-numeric storage class "
                f"({bad_types}) in a PG-numeric column -- would be REJECTED "
                f"or silently miscoerced by COPY. Sample values: "
                f"{[s[0] for s in samples]}"
            )
            result.ok = False

    return result, rec


# ═══════════════════════════════════════════════════════════════════════════
# Copy
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class CopyResult:
    table: str
    rows_copied: int
    sqlite_row_count: int
    pg_row_count: int
    row_count_ok: bool
    spot_hash_ok: bool
    elapsed_s: float


def _row_hash(values: Iterable[Any]) -> str:
    """Deterministic hash of one row's values, independent of Python type
    (SQLite gives floats/ints/str/None; psycopg dict_row gives the same
    Postgres-side) -- str() every value so 1 vs 1.0 vs "1" are the only
    ambiguity, which cannot occur here since both sides read/write the
    same declared column type."""
    canon = "\x1f".join("\x00" if v is None else str(v) for v in values)
    return hashlib.sha256(canon.encode("utf-8", "surrogateescape")).hexdigest()


def copy_table(
    sconn: sqlite3.Connection,
    pconn,
    table: str,
    plan: TablePlan,
    rec: Reconciled,
    window_days: int,
    batch_size: int,
) -> CopyResult:
    start = time.monotonic()
    where, params = _window_where(plan, window_days)
    cols_sql = ", ".join(rec.columns)
    select_sql = f"SELECT {cols_sql} FROM {table} {where}"

    scur = sconn.cursor()
    scur.execute(select_sql, params)

    with pconn.cursor() as pcur:
        # CASCADE: harmless no-op for the 64 of 65 tables nothing
        # references (TRUNCATE CASCADE only touches tables that actually
        # have an FK pointing at this one). Needed for the one real case
        # (osint_items.scope_id -> osint_scopes.id, the only FK in the
        # schema -- grep "REFERENCES" across src/common/pg_schema/*.sql):
        # plain TRUNCATE on osint_scopes fails outright once osint_items
        # holds rows referencing it, which a bare per-table TRUNCATE hits
        # on every re-run after the first. _fk_ordered() above still
        # copies parent before child so the cascade-emptied child is
        # immediately repopulated later in the same run -- if a caller
        # copies osint_scopes WITHOUT osint_items in the same --tables
        # invocation, osint_items is emptied by this cascade and stays
        # empty until it too is copied; --list / this comment is the
        # documentation of that, there is no silent partial state.
        pcur.execute(f"TRUNCATE TABLE {table} CASCADE")
        copy_sql = f"COPY {table} ({cols_sql}) FROM STDIN"
        rows_copied = 0
        with pcur.copy(copy_sql) as copy:
            while True:
                batch = scur.fetchmany(batch_size)
                if not batch:
                    break
                for row in batch:
                    copy.write_row(tuple(row))
                rows_copied += len(batch)

        if rec.identity_column:
            # Keep the identity sequence ahead of the max copied value so
            # nothing here collides with a value assigned later, at Phase
            # 3 cutover, by a fresh INSERT on this same table. Harmless
            # no-op if the table is empty or has no identity column.
            pcur.execute(
                f"SELECT setval(pg_get_serial_sequence(%s, %s), "
                f"COALESCE((SELECT MAX({rec.identity_column}) FROM {table}), 1))",
                (table, rec.identity_column),
            )
    pconn.commit()

    verify = verify_table(sconn, pconn, table, plan, rec, window_days)
    elapsed = time.monotonic() - start
    return CopyResult(
        table=table,
        rows_copied=rows_copied,
        sqlite_row_count=verify[0],
        pg_row_count=verify[1],
        row_count_ok=verify[0] == verify[1],
        spot_hash_ok=verify[2],
        elapsed_s=elapsed,
    )


def verify_table(
    sconn: sqlite3.Connection,
    pconn,
    table: str,
    plan: TablePlan,
    rec: Reconciled,
    window_days: int,
) -> tuple[int, int, bool]:
    """Row-count + spot-hash verification. Returns (sqlite_count, pg_count,
    spot_hash_matches). Re-runnable independently of copy_table() via
    --verify-only, so a suspect table can be re-checked without re-copying
    it."""
    where, params = _window_where(plan, window_days)
    cols_sql = ", ".join(rec.columns)
    pk_cols = sqlite_pk_columns(sconn, table) or rec.columns[:1]
    pk_cols = [c for c in pk_cols if c in rec.columns] or rec.columns[:1]
    order_sql = ", ".join(pk_cols)

    s_count = sconn.execute(f"SELECT COUNT(*) FROM {table} {where}", params).fetchone()[0]

    with pconn.cursor() as pcur:
        pcur.execute(f"SELECT COUNT(*) FROM {table}")
        p_count = pcur.fetchone()[0]

    if s_count == 0:
        return s_count, p_count, (p_count == 0)

    # Evenly-spaced sample by row position, ordered by PK -- not a random
    # sample, so it is exactly reproducible across the two engines without
    # needing to agree on a random seed.
    #
    # 2026-09-18: stream the cursor and keep only the sampled rows, rather
    # than .fetchall() the entire windowed result set and slice it in
    # Python. Found live during the flight_events re-verify: a 90-day
    # window's full row set materialized in Python pushed this process to
    # 4.5+ GB RSS and drove the box into swap (observed via `free -h` while
    # it ran) just to pick out 200 sample rows -- SPOT_HASH_SAMPLE never
    # needed more than n rows in memory at once. Every other time-series
    # table in the 65-table set (train_events, vessel_events, acars_messages,
    # tbfm_sequences, surface_movement_events, ...) hits this same verify
    # path in the mandatory full --verify-only pass, so this isn't a
    # one-table fix.
    n = min(SPOT_HASH_SAMPLE, s_count)
    stride = max(1, s_count // n)
    s_cur = sconn.execute(
        f"SELECT {cols_sql} FROM {table} {where} ORDER BY {order_sql}",
        params,
    )
    sampled = []
    for i, row in enumerate(s_cur):
        if i % stride == 0:
            sampled.append(row)
            if len(sampled) >= n:
                break
    s_cur.close()
    s_hashes = sorted(_row_hash(row) for row in sampled)

    pk_where = " AND ".join(f"{c} = %s" for c in pk_cols)
    p_hashes = []
    missing_in_pg = 0
    with pconn.cursor() as pcur:
        for row in sampled:
            pk_values = [row[rec.columns.index(c)] for c in pk_cols]
            pcur.execute(
                f"SELECT {cols_sql} FROM {table} WHERE {pk_where}", pk_values
            )
            prow = pcur.fetchone()
            if prow is None:
                missing_in_pg += 1
            else:
                p_hashes.append(_row_hash(prow))
    # Every sampled SQLite row must exist in PG with an identical column-
    # value hash. Order-independent (sorted) since the two engines are not
    # guaranteed to enumerate a matching-PK lookup in the same order.
    spot_ok = missing_in_pg == 0 and sorted(p_hashes) == s_hashes
    return s_count, p_count, spot_ok


def _fk_ordered(pconn, tables: list[str]) -> list[str]:
    """Reorder `tables` so a foreign-key parent is always copied (and thus
    TRUNCATEd+reloaded) before its child, among the tables actually
    requested. Discovered live 2026-09-18: osint_items.scope_id REFERENCES
    osint_scopes(id) is the only FK in the whole 65-table schema (grep
    "REFERENCES" across src/common/pg_schema/*.sql), but copying
    osint_items before osint_scopes fails the FK constraint outright --
    exactly the kind of thing that only shows up by actually running the
    copy, not by reading the schema. Built generically off
    information_schema rather than hardcoding the one known pair, so a
    future FK add is handled automatically. A dependency on a table NOT in
    `tables` (parent already loaded in a prior run, or intentionally out
    of scope) is left alone -- Postgres will simply enforce the constraint
    against whatever is already there, same as any other run."""
    with pconn.cursor() as cur:
        cur.execute(
            """
            SELECT tc.table_name AS child, ccu.table_name AS parent
            FROM information_schema.table_constraints tc
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = %s
            """,
            (_CURRENT_SCHEMA,),
        )
        edges = [(c, p) for c, p in cur.fetchall() if c != p]

    requested = set(tables)
    deps: dict[str, set[str]] = {t: set() for t in tables}
    for child, parent in edges:
        if child in requested and parent in requested:
            deps[child].add(parent)

    ordered: list[str] = []
    placed: set[str] = set()
    remaining = list(tables)
    while remaining:
        progressed = False
        for t in list(remaining):
            if deps[t] <= placed:
                ordered.append(t)
                placed.add(t)
                remaining.remove(t)
                progressed = True
        if not progressed:
            # A cycle would mean the schema has one, which none of the
            # single-FK reality above supports -- fail loud rather than
            # silently drop the ordering guarantee.
            raise RuntimeError(
                f"migrate-sqlite-to-pg.py: FK dependency cycle or "
                f"unresolvable ordering among {remaining}"
            )
    return ordered


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════


def _sqlite_connect(path: str) -> sqlite3.Connection:
    # mode=ro: the OS refuses any write, at the sqlite3 API level, before
    # it ever gets near the file -- this script structurally cannot write
    # to the source database.
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = None  # plain tuples, matching COPY's positional write_row()
    return conn


def _pg_connect(database: Optional[str]):
    import psycopg

    conninfo = db_backend._pg_conninfo()  # noqa: SLF001 -- same reuse pg_migrate.py does
    if database:
        parts = [p for p in conninfo.split(" ") if not p.startswith("dbname=")]
        parts.append(f"dbname={database}")
        conninfo = " ".join(parts)
    conn = psycopg.connect(conninfo, autocommit=False)
    # 2026-09-18: config/postgresql.conf's statement_timeout=60s is a
    # deliberate OLTP guard rail sized for the platform's normal write
    # pattern (small, fast, frequent transactions) -- it exists to kill
    # runaway queries before they hold locks and block concurrent
    # writers, the exact failure mode this migration replaces SQLite to
    # avoid. A one-time bulk COPY of a large table is a legitimate but
    # completely different workload shape that guard rail was never
    # sized for -- confirmed live: flight_events' COPY got killed by it.
    # This is a SESSION-scoped override on this script's own connection
    # only; the production setting in postgresql.conf (and every other
    # connection's default) is untouched.
    #
    # 2026-09-18 follow-up: 600s wasn't enough either -- flight_events'
    # COPY got to row 427,007 (of ~1M+ in the 90-day window) before that
    # timeout killed it too, real progress at ~712 rows/s implying the
    # full copy needs something like 20-25 min. Rather than guess another
    # fixed number for whatever the next largest table turns out to be,
    # disable the timeout entirely (0 = no limit) for this script's own
    # connection -- this is a deliberate, supervised, one-time bulk-load
    # tool, not a live application path, so there's no runaway-query risk
    # this guard rail needs to protect against here.
    #
    # 2026-09-18 follow-up #2: verify_table() opens this connection (row-
    # count check) before doing the SQLite-side windowed ORDER BY scan +
    # stride sample, which for a large time-series table (flight_events:
    # ~10-12 min, no index covers the WHERE-window + ORDER-BY-PK combo)
    # leaves this session idle-in-transaction the whole time. Confirmed
    # live: idle_in_transaction_session_timeout=60s (same postgresql.conf
    # OLTP guard rail class as statement_timeout, sized for the same
    # normal write pattern) killed the connection well before the SQLite
    # side finished, failing on the very next PG query (the per-row PK
    # spot-check lookup). Same justification as statement_timeout above:
    # session-scoped only, this script is a supervised one-time tool, not
    # a live application path with the runaway-idle-transaction risk this
    # guard rail exists to catch.
    with conn.cursor() as _cur:
        _cur.execute("SET statement_timeout = 0")
        _cur.execute("SET idle_in_transaction_session_timeout = 0")
    conn.commit()
    return conn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tables", default=None, help="Comma-separated table names (default: all 65 in TABLE_PLAN).")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS, help=f"Retention window for time_series tables (default {DEFAULT_WINDOW_DAYS}).")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"SQLite fetchmany() batch size feeding COPY (default {DEFAULT_BATCH_SIZE}).")
    ap.add_argument("--sqlite-path", default=None, help="Override DISPATCH_DB (default: config.db_path()).")
    ap.add_argument("--database", default=None, help="Override DISPATCH_PG_DB (e.g. the scratch corporatetraveldc_pgphase1_test database).")
    ap.add_argument("--schema", default="public", help="Postgres schema (default: public).")
    ap.add_argument("--list", action="store_true", help="Print the table plan (kind, window column, evidence) and exit.")
    ap.add_argument("--audit-only", action="store_true", help="Run the schema-drift + numeric-column audit only; copy nothing.")
    ap.add_argument("--dry-run", action="store_true", help="Print what would be copied (row counts under the window) without writing.")
    ap.add_argument("--verify-only", action="store_true", help="Skip copying; only run row-count + spot-hash verification against what is already in PG.")
    ap.add_argument("--yes", action="store_true", help="Skip the interactive confirmation before writing to Postgres.")
    args = ap.parse_args()

    global _CURRENT_SCHEMA
    _CURRENT_SCHEMA = args.schema

    if args.tables:
        requested = [t.strip() for t in args.tables.split(",") if t.strip()]
        unknown = [t for t in requested if t not in TABLE_PLAN]
        if unknown:
            ref_hit = [t for t in unknown if t in db_backend.REFERENCE_TABLES]
            if ref_hit:
                print(f"REFUSING: {ref_hit} are reference tables (SQLite-only, "
                      f"docs/POSTGRES_MIGRATION.md §2) -- never copied.", file=sys.stderr)
            other = [t for t in unknown if t not in ref_hit]
            if other:
                print(f"Unknown table(s) not in TABLE_PLAN: {other}", file=sys.stderr)
            return 2
        tables = requested
    else:
        tables = sorted(TABLE_PLAN)

    if args.list:
        for t in sorted(TABLE_PLAN):
            p = TABLE_PLAN[t]
            if p.kind == "time_series":
                print(f"{t:38s} time_series  {p.ts_col} ({p.ts_type})  -- {p.evidence}")
            else:
                print(f"{t:38s} state                              -- {p.evidence}")
        return 0

    sqlite_path = args.sqlite_path or config.db_path()
    sconn = _sqlite_connect(sqlite_path)
    pconn = _pg_connect(args.database)
    if args.schema != "public":
        with pconn.cursor() as cur:
            cur.execute(f'SET search_path TO "{args.schema}"')

    try:
        if args.verify_only:
            print(f"{'table':38s} {'sqlite':>10s} {'pg':>10s} {'counts':>8s} {'hash':>6s}")
            all_ok = True
            for t in tables:
                plan = TABLE_PLAN[t]
                rec = reconcile_columns(sconn, pconn, t)
                s_count, p_count, spot_ok = verify_table(sconn, pconn, t, plan, rec, args.window_days)
                ok = (s_count == p_count) and spot_ok
                all_ok &= ok
                print(f"{t:38s} {s_count:>10d} {p_count:>10d} {'OK' if s_count==p_count else 'MISMATCH':>8s} {'OK' if spot_ok else 'FAIL':>6s}")
            return 0 if all_ok else 1

        # ── audit (always runs first, even for a plain copy) ──────────
        print("Auditing schema drift and numeric-column content...")
        audit_ok = True
        plans_recs: dict[str, tuple[AuditResult, Reconciled]] = {}
        for t in tables:
            plan = TABLE_PLAN[t]
            result, rec = audit_table(sconn, pconn, t, plan, args.window_days)
            plans_recs[t] = (result, rec)
            if result.schema_warnings:
                for w in result.schema_warnings:
                    print(f"  [{t}] WARN: {w}")
            if result.numeric_violations:
                audit_ok = False
                for v in result.numeric_violations:
                    print(f"  [{t}] AUDIT FAIL: {v}")
        if not audit_ok:
            print(
                "\nAudit found numeric-column content that would corrupt on "
                "copy. Tables with violations are SKIPPED below -- fix the "
                "source data (or the classification/column mapping if this "
                "is a false positive) and re-run.",
                file=sys.stderr,
            )
        else:
            print("Audit clean: no schema drift beyond documented column "
                  "differences, no non-numeric values in PG-numeric columns.")

        if args.audit_only:
            return 0 if audit_ok else 1

        copyable = _fk_ordered(pconn, [t for t in tables if plans_recs[t][0].ok])
        skipped = [t for t in tables if not plans_recs[t][0].ok]

        if args.dry_run:
            print(f"\nDry run -- would copy {len(copyable)} table(s) "
                  f"(window={args.window_days}d for time_series tables), "
                  f"skip {len(skipped)} (audit failed): {skipped}")
            for t in copyable:
                plan = TABLE_PLAN[t]
                rec = plans_recs[t][1]
                where, params = _window_where(plan, args.window_days)
                n = sconn.execute(f"SELECT COUNT(*) FROM {t} {where}", params).fetchone()[0]
                print(f"  {t:38s} {plan.kind:12s} {n:>10d} row(s) to copy")
            return 0

        if not args.yes:
            print(f"\nAbout to TRUNCATE + reload {len(copyable)} table(s) in "
                  f"Postgres database={args.database or config.get('DISPATCH_PG_DB')} "
                  f"schema={args.schema}. SQLite is never written. Continue? [y/N] ", end="")
            if input().strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return 1

        print(f"\nCopying {len(copyable)} table(s)...")
        results: list[CopyResult] = []
        failures: list[str] = []
        for t in copyable:
            plan = TABLE_PLAN[t]
            rec = plans_recs[t][1]
            try:
                r = copy_table(sconn, pconn, t, plan, rec, args.window_days, args.batch_size)
                results.append(r)
                status = "OK" if (r.row_count_ok and r.spot_hash_ok) else "VERIFY-FAIL"
                print(f"  {t:38s} copied={r.rows_copied:>9d} "
                      f"sqlite={r.sqlite_row_count:>9d} pg={r.pg_row_count:>9d} "
                      f"{status:>12s} ({r.elapsed_s:.2f}s)")
                if status != "OK":
                    failures.append(t)
            except Exception as exc:  # noqa: BLE001 -- report and keep going per-table
                pconn.rollback()
                print(f"  {t:38s} FAILED: {exc}", file=sys.stderr)
                failures.append(t)

        print(f"\n{len(results)} copied, {len(skipped)} skipped (audit), "
              f"{len(failures)} failed/verify-mismatch.")
        if skipped:
            print(f"Skipped (re-run with --audit-only for detail): {skipped}")
        if failures:
            print(f"Failed/mismatched (retry individually with --tables): {failures}")
        return 0 if not failures and not skipped else 1
    finally:
        sconn.close()
        pconn.close()


if __name__ == "__main__":
    raise SystemExit(main())
