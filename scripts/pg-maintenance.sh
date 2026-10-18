#!/usr/bin/env bash
# scripts/pg-maintenance.sh -- CONCURRENTLY index work on the production
# Postgres (corporatetraveldc-pgsql), which a migration cannot do.
#
# 2026-10-18 (lean-out L1, operator-approved "start everything as planned").
# scripts/pg_migrate.py runs each migration file inside one transaction, and
# CREATE INDEX CONCURRENTLY / REINDEX ... CONCURRENTLY refuse to run inside a
# transaction block. A plain CREATE INDEX blocks every writer on the table for
# the whole build (minutes on the 5.4 GB flight_events). So these statements
# live here, each sent as its own psql -c (autocommit), and the relay calls
# this script on the line after `scripts/relay-wait-load.sh` (relay-lint
# enforces that: pg-maintenance.sh is a heavy step).
#
# Subcommands (each idempotent and rerun-safe):
#   status                               read-only: state + size of every index below
#   create-indexes-concurrently [NAME..] build the missing read-path indexes (all, or
#                                        the named ones). A valid index is skipped;
#                                        an INVALID one (a build that failed or was
#                                        cancelled) is dropped CONCURRENTLY and rebuilt.
#   reindex-flight-events-updated-at     REINDEX INDEX CONCURRENTLY the bloated
#                                        idx_flight_events_updated_at (2,151 MB for
#                                        ~966k rows on 2026-10-18; the pkey is 89 MB).
#                                        Skipped while the index is under
#                                        PG_MAINT_REINDEX_MIN_MB (default 512): a rerun
#                                        after a success does nothing. Leftover
#                                        *_ccnew* indexes from a failed run are dropped
#                                        first. The key is updated on every upsert, so
#                                        it re-bloats: re-check monthly with `status`.
#
# Sessions: statement_timeout=0 (a build outlives the 60 s global) and
# lock_timeout=300s (a CONCURRENTLY build first waits for every older
# transaction; 300 s is past the 60 s statement_timeout every other session
# has). A CONCURRENTLY build does not block reads or writes on the table.
# Output: index names, states, sizes and the DDL only -- never credentials;
# the password stays inside the container ($POSTGRES_PASSWORD there).
#
# Test hooks: PG_MAINT_PODMAN (default podman), PG_MAINT_CONTAINER (default
# corporatetraveldc-pgsql), PG_MAINT_DRY_RUN=1 (print the write statements,
# run none; the state reads still run).
set -euo pipefail

PODMAN="${PG_MAINT_PODMAN:-podman}"
CONTAINER="${PG_MAINT_CONTAINER:-corporatetraveldc-pgsql}"
DRY="${PG_MAINT_DRY_RUN:-0}"
REINDEX_MIN_MB="${PG_MAINT_REINDEX_MIN_MB:-512}"
SESSION_OPTS="-c statement_timeout=0 -c lock_timeout=300s"

# name|table|DDL after "ON " -- the only indexes this script will ever touch.
INDEXES=(
    # get_active_nas_programs() `WHERE active=1 ORDER BY type, facility` (seq scan
    # of a ~305 MB heap for a handful of rows) and the REST/TFMS deactivation reads.
    "idx_nas_programs_active|nas_programs|public.nas_programs (type, facility) WHERE active = 1"
    # airline_time_fidelity_collect.py siblings() (`callsign = ? AND gufi <> ? AND
    # updated_at BETWEEN`) and watched_scope() (`callsign IN (...) AND updated_at >=`):
    # 14,452 seq scans / 4.37 B tuples in 59 h.
    "idx_flight_ooooi_times_callsign_updated|flight_ooooi_times|public.flight_ooooi_times (callsign, updated_at)"
    # watched_scope() `upper(registration) IN (...) AND updated_at >= now-7d` (hits the
    # 60 s statement_timeout since 2026-10-17 18:30Z). INCLUDE makes the
    # `airline || flight_num` projection index-only where the visibility map allows.
    "idx_flight_events_registration_upper|flight_events|public.flight_events (upper(registration), updated_at) INCLUDE (airline, flight_num) WHERE registration IS NOT NULL"
    # Not here: a partial "pending cancellation" index on tfms_plan_removals for
    # db_swim.sweep_confirm_removals(). EXPLAIN on a 340k-row copy with the live
    # distribution (2026-10-18) kept idx_tfms_plan_removals_pending, whose Index
    # Cond (confirmed_at IS NULL, reinstated_at IS NULL, kind) already seeks
    # straight to the ~7k pending cancellations; the partial index was never chosen.
    # db.get_flight_plan_by_flight_num() `flight_num = ? AND origin IN (...)`: flight_num
    # was only the second column of (airline, flight_num), so every call walked the index.
    "idx_flight_events_flight_num_origin|flight_events|public.flight_events (flight_num, origin)"
)
REINDEX_TARGET="idx_flight_events_updated_at"

die() { echo "pg-maintenance: $*" >&2; exit 1; }

# One statement per psql -c: autocommit, never an implicit transaction block.
psql_one() {
    "$PODMAN" exec -i -e PGOPTIONS="$SESSION_OPTS" "$CONTAINER" sh -c \
        'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' \
        sh "$1" </dev/null
}

write_one() {
    echo "pg-maintenance: SQL: $1"
    if [[ "$DRY" == "1" ]]; then
        echo "pg-maintenance: dry run -- not executed"
        return 0
    fi
    psql_one "$1" >/dev/null
}

# valid | invalid | absent
index_state() {
    local out
    out="$(psql_one "SELECT CASE WHEN i.indisvalid AND i.indisready THEN 'valid' ELSE 'invalid' END FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relname = '$1'")"
    [[ -n "$out" ]] && echo "$out" || echo "absent"
}

index_mb() {
    psql_one "SELECT COALESCE((SELECT pg_relation_size(c.oid) / 1048576 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relname = '$1'), 0)"
}

entry_for() {
    local e
    for e in "${INDEXES[@]}"; do
        [[ "${e%%|*}" == "$1" ]] && { echo "$e"; return 0; }
    done
    return 1
}

create_one() {
    local entry="$1" name rest ddl state
    name="${entry%%|*}"
    rest="${entry#*|}"
    ddl="${rest#*|}"
    state="$(index_state "$name")"
    case "$state" in
        valid)
            echo "pg-maintenance: $name valid -- skip"
            return 0 ;;
        invalid)
            echo "pg-maintenance: $name INVALID (an earlier build failed) -- dropping it first"
            write_one "DROP INDEX CONCURRENTLY IF EXISTS public.$name" ;;
        absent) ;;
        *) die "unexpected state '$state' for $name" ;;
    esac
    write_one "CREATE INDEX CONCURRENTLY IF NOT EXISTS $name ON $ddl"
    [[ "$DRY" == "1" ]] && return 0
    state="$(index_state "$name")"
    [[ "$state" == "valid" ]] || die "$name is '$state' after the build -- rerun (it is dropped and rebuilt)"
    echo "pg-maintenance: $name valid ($(index_mb "$name") MB)"
}

cmd_status() {
    local e name
    for e in "${INDEXES[@]}"; do
        name="${e%%|*}"
        echo "$name $(index_state "$name") $(index_mb "$name")MB"
    done
    echo "$REINDEX_TARGET $(index_state "$REINDEX_TARGET") $(index_mb "$REINDEX_TARGET")MB"
}

cmd_create() {
    local e
    if (( $# == 0 )); then
        for e in "${INDEXES[@]}"; do create_one "$e"; done
        return 0
    fi
    local n
    for n in "$@"; do
        e="$(entry_for "$n")" || die "unknown index '$n' (see: $0 status)"
        create_one "$e"
    done
}

cmd_reindex() {
    local state mb leftovers l
    state="$(index_state "$REINDEX_TARGET")"
    [[ "$state" == "valid" ]] || die "$REINDEX_TARGET is '$state' -- not reindexing; check by hand"
    # A failed REINDEX CONCURRENTLY leaves an invalid <name>_ccnew[N] copy behind.
    leftovers="$(psql_one "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND NOT i.indisvalid AND c.relname LIKE '${REINDEX_TARGET}_ccnew%'")"
    for l in $leftovers; do
        [[ "$l" =~ ^${REINDEX_TARGET}_ccnew[0-9]*$ ]] || die "refusing to drop unexpected index name '$l'"
        echo "pg-maintenance: leftover $l from an earlier failed reindex -- dropping"
        write_one "DROP INDEX CONCURRENTLY IF EXISTS public.$l"
    done
    mb="$(index_mb "$REINDEX_TARGET")"
    if (( mb < REINDEX_MIN_MB )); then
        echo "pg-maintenance: $REINDEX_TARGET is ${mb} MB (< ${REINDEX_MIN_MB} MB) -- skip"
        return 0
    fi
    echo "pg-maintenance: $REINDEX_TARGET is ${mb} MB before"
    write_one "REINDEX INDEX CONCURRENTLY public.$REINDEX_TARGET"
    [[ "$DRY" == "1" ]] && return 0
    state="$(index_state "$REINDEX_TARGET")"
    [[ "$state" == "valid" ]] || die "$REINDEX_TARGET is '$state' after the reindex"
    echo "pg-maintenance: $REINDEX_TARGET valid, $(index_mb "$REINDEX_TARGET") MB after"
}

sub="${1:-}"
[[ -n "$sub" ]] || die "usage: $0 status | create-indexes-concurrently [NAME...] | reindex-flight-events-updated-at"
shift
case "$sub" in
    status) cmd_status ;;
    create-indexes-concurrently) cmd_create "$@" ;;
    reindex-flight-events-updated-at) cmd_reindex ;;
    *) die "unknown subcommand '$sub'" ;;
esac
