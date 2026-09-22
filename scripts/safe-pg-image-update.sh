#!/usr/bin/env bash
# scripts/safe-pg-image-update.sh
# Standing safe-update procedure for the postgres:16-alpine image backing
# corporatetraveldc-pgsql -- built 2026-09-19 (operator directive), the
# night of the Postgres cutover, specifically to replace blindly running
# `podman auto-update` against our own live production database.
#
# Why this exists: `podman auto-update --dry-run` flagged corporatetraveldc-
# pgsql itself as pending on the same run as nextcloud-app/nextcloud-db --
# scripts/weekly-external-image-update.sh's own header documents a real
# past incident where a blind auto-update jumped Nextcloud a MAJOR version
# on a rolling tag. Postgres stays on major version 16 here (same data
# directory format across 16.x patch releases, no dump/restore needed for
# a real in-place update) but "the tag didn't change" is not the same
# guarantee as "nothing changed" -- this script proves it on a real,
# isolated copy of the live schema + a data sample before ever touching
# the live container.
#
# Sequence:
#   1. pull the new image standalone (never touches the running container)
#   2. spin up an isolated canary instance: scratch port, scratch volume,
#      the SAME tuned postgresql.conf/pg_hba.conf as production
#   3. load the live schema (pg_dump --schema-only, read-only against live)
#      plus a small real-data sample (LIMIT per table) into the canary
#   4. validate: version string, per-table row counts match what was
#      copied, canary's own postgres log scanned for WARNING/ERROR/FATAL
#   5. report PASS/FAIL and STOP -- this script never touches the live
#      container on its own. A clean run is a precondition, not a trigger.
#   6. a SEPARATE `apply` invocation (explicit operator step) does the
#      actual in-place update: restart corporatetraveldc-pgsql, which
#      picks up the already-pulled image and reuses the existing data
#      volume unchanged.
#
# Usage:
#   scripts/safe-pg-image-update.sh test      # phases 1-5, stops there
#   scripts/safe-pg-image-update.sh apply     # phase 6 -- only after a clean 'test'
#   scripts/safe-pg-image-update.sh cleanup   # remove the canary if left behind
#
# 2026-09-19: generalized via env-var overrides so the same script covers
# any postgres:16-alpine-backed container, not just corporatetraveldc-pgsql
# -- e.g. nextcloud-db (different network, DB/user, no custom tuned
# config -- it runs the image's own stock defaults, so PGCONF/HBACONF are
# left unset there rather than pointing at corporatetraveldc's own tuned
# files, which nextcloud-db does not actually run):
#   LIVE_CONTAINER=nextcloud-db LIVE_SERVICE=nextcloud-db.service \
#   PG_DB=nextcloud PG_USER=nextcloud CANARY_PORT=15433 \
#   LIVE_SECRETS=~/.config/nextcloud/nextcloud-secrets.env \
#   PGCONF= HBACONF= \
#   scripts/safe-pg-image-update.sh test
set -uo pipefail

IMAGE="docker.io/library/postgres:16-alpine"
: "${LIVE_CONTAINER:=corporatetraveldc-pgsql}"
: "${LIVE_SERVICE:=corporatetraveldc-pgsql.service}"
: "${CANARY_CONTAINER:=${LIVE_CONTAINER}-canary}"
: "${CANARY_VOLUME:=${LIVE_CONTAINER}-canary-data}"
: "${CANARY_PORT:=15432}"
: "${PG_DB:=corporatetraveldc}"
: "${PG_USER:=dispatch}"
: "${SAMPLE_LIMIT:=500}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${PGCONF=${REPO_DIR}/config/postgresql.conf}"
: "${HBACONF=${REPO_DIR}/config/pg_hba.conf}"
: "${LIVE_SECRETS:=/etc/corporatetraveldc/pgsql-secrets.env}"

log() { echo "[safe-pg-image-update] $*"; }
die() { echo "[safe-pg-image-update] ERROR: $*" >&2; exit 1; }

# pg_hba.conf requires scram-sha-256 even on the local socket -- psql/
# pg_dump need PGPASSWORD explicitly (NOT the same variable as the image's
# own bootstrapping POSTGRES_PASSWORD); podman exec -e sets it per-exec.
LIVE_PW="$(grep -m1 '^POSTGRES_PASSWORD=' "${LIVE_SECRETS}" | cut -d= -f2-)"
[[ -n "${LIVE_PW}" ]] || die "could not read POSTGRES_PASSWORD from ${LIVE_SECRETS}"

cleanup_canary() {
    podman rm -f "${CANARY_CONTAINER}" >/dev/null 2>&1 || true
    podman volume rm -f "${CANARY_VOLUME}" >/dev/null 2>&1 || true
    log "canary container+volume removed"
}

cmd_test() {
    log "pulling ${IMAGE}..."
    podman pull "${IMAGE}" || die "pull failed"

    live_digest=$(podman inspect "${LIVE_CONTAINER}" --format '{{.Image}}' 2>/dev/null) \
        || die "live container ${LIVE_CONTAINER} not found/running"
    new_digest=$(podman image inspect "${IMAGE}" --format '{{.Id}}')
    log "live container image digest: ${live_digest}"
    log "freshly pulled image digest: ${new_digest}"
    if [[ "${live_digest}" == "${new_digest}" ]]; then
        log "no update available -- live container already runs this exact image. Nothing to test."
        exit 0
    fi

    log "new version string: $(podman run --rm "${IMAGE}" postgres --version)"
    log "live version string: $(podman exec -e PGPASSWORD="${LIVE_PW}" "${LIVE_CONTAINER}" postgres --version)"

    log "cleaning up any stale canary from a previous run..."
    cleanup_canary

    canary_pw="$(head -c18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c24)"
    log "starting isolated canary (${CANARY_CONTAINER}) on 127.0.0.1:${CANARY_PORT}, scratch volume ${CANARY_VOLUME}..."
    run_args=(-d --name "${CANARY_CONTAINER}"
        -e POSTGRES_DB="${PG_DB}"
        -e POSTGRES_USER="${PG_USER}"
        -e POSTGRES_PASSWORD="${canary_pw}"
        -e PGDATA=/var/lib/postgresql/data/pgdata
        -e TZ=UTC
        -v "${CANARY_VOLUME}:/var/lib/postgresql/data"
        -p "127.0.0.1:${CANARY_PORT}:5432"
        --shm-size=512m)
    exec_args=()
    # Only mount/apply a custom tuned config if this profile actually sets
    # one (corporatetraveldc-pgsql does; nextcloud-db runs the image's own
    # stock defaults -- mounting corporatetraveldc's tuned files onto a
    # nextcloud-db canary would test a config nextcloud-db doesn't actually
    # run, defeating the point of the canary).
    if [[ -n "${PGCONF}" && -n "${HBACONF}" ]]; then
        run_args+=(-v "${PGCONF}:/etc/postgresql/postgresql.conf:ro,Z"
                    -v "${HBACONF}:/etc/postgresql/pg_hba.conf:ro,Z")
        exec_args+=(postgres -c config_file=/etc/postgresql/postgresql.conf)
    fi
    podman run "${run_args[@]}" "${IMAGE}" "${exec_args[@]}" \
        >/dev/null || die "canary start failed"

    log "waiting for canary to accept connections..."
    ready=0
    for _ in $(seq 1 30); do
        if podman exec -e PGPASSWORD="${canary_pw}" "${CANARY_CONTAINER}" pg_isready -U "${PG_USER}" -d "${PG_DB}" >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 2
    done
    [[ "${ready}" -eq 1 ]] || { podman logs "${CANARY_CONTAINER}" 2>&1 | tail -30; die "canary never became ready"; }
    log "canary ready: $(podman exec -e PGPASSWORD="${canary_pw}" "${CANARY_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -tAc 'SELECT version();')"

    log "dumping live schema (read-only against ${LIVE_CONTAINER})..."
    # --no-owner --no-acl: 2026-09-19, found live on the nextcloud-db
    # profile -- its schema dump includes ALTER TABLE ... OWNER TO oc_admin
    # (an internal Nextcloud-created role the canary never creates), which
    # aborted the schema apply. This is a functional smoke test (does the
    # new Postgres version host our real schema+data correctly), not an
    # ownership/ACL fidelity check -- the canary owns everything as its own
    # POSTGRES_USER, which is what matters for that question. Real
    # disaster-recovery fidelity is the separate full pg_dump backup, not
    # this script.
    podman exec -e PGPASSWORD="${LIVE_PW}" "${LIVE_CONTAINER}" pg_dump --schema-only --no-owner --no-acl -U "${PG_USER}" -d "${PG_DB}" \
        > /tmp/pg-canary-schema.sql || die "schema dump failed"
    log "schema dump: $(wc -l < /tmp/pg-canary-schema.sql) lines"

    log "applying schema to canary..."
    podman exec -i -e PGPASSWORD="${canary_pw}" "${CANARY_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -v ON_ERROR_STOP=1 \
        < /tmp/pg-canary-schema.sql > /tmp/pg-canary-schema-apply.log 2>&1
    schema_rc=$?
    if [[ ${schema_rc} -ne 0 ]]; then
        log "SCHEMA APPLY FAILED -- see /tmp/pg-canary-schema-apply.log"
        tail -30 /tmp/pg-canary-schema-apply.log
        log "RESULT: FAIL -- canary left running for inspection (podman logs/exec ${CANARY_CONTAINER})"
        exit 1
    fi

    log "loading a ${SAMPLE_LIMIT}-row real-data sample per table (FK-parent-first order)..."
    # 2026-09-19: alphabetical table order broke on the very first real run --
    # osint_items (FK to osint_scopes) sorts before osint_scopes alphabetically,
    # so its sample \copy aborted on the FK constraint (0 rows landed, silently
    # masked because the per-table error log was being overwritten each
    # iteration, not appended -- fixed below too). Same root cause the real
    # migration script's _fk_ordered() already solves; same fix here rather
    # than a special case, since a future FK add should be handled
    # automatically, not require remembering to update this script too.
    tables_raw=$(podman exec -e PGPASSWORD="${LIVE_PW}" "${LIVE_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -tAc \
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
    fk_edges=$(podman exec -e PGPASSWORD="${LIVE_PW}" "${LIVE_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -tAc \
        "SELECT tc.table_name || ' ' || ccu.table_name
         FROM information_schema.table_constraints tc
         JOIN information_schema.constraint_column_usage ccu
           ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
         WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
           AND tc.table_name != ccu.table_name")
    tables=$(python3 -c "
import sys
raw = '''${tables_raw}'''.split()
edges = [tuple(l.split()) for l in '''${fk_edges}'''.splitlines() if l.strip()]
# simple stable topo-ish sort: repeatedly move any child before its parent's
# position if it's currently ahead of it -- good enough for this schema's
# single known FK, generalizes to more without special-casing.
order = list(raw)
changed = True
while changed:
    changed = False
    for child, parent in edges:
        if child in order and parent in order and order.index(child) < order.index(parent):
            order.remove(child)
            order.insert(order.index(parent) + 1, child)
            changed = True
print(' '.join(order))
")
    fail=0
    mismatch=0
    err_log_dir="$(mktemp -d)"
    for t in ${tables}; do
        live_count=$(podman exec -e PGPASSWORD="${LIVE_PW}" "${LIVE_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -tAc \
            "SELECT count(*) FROM (SELECT 1 FROM ${t} LIMIT ${SAMPLE_LIMIT}) s")
        podman exec -e PGPASSWORD="${LIVE_PW}" "${LIVE_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -c \
            "\copy (SELECT * FROM ${t} LIMIT ${SAMPLE_LIMIT}) TO STDOUT" 2>"${err_log_dir}/${t}.err" \
            | podman exec -i -e PGPASSWORD="${canary_pw}" "${CANARY_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -c \
            "\copy ${t} FROM STDIN" >/dev/null 2>>"${err_log_dir}/${t}.err"
        canary_count=$(podman exec -e PGPASSWORD="${canary_pw}" "${CANARY_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -tAc \
            "SELECT count(*) FROM ${t}")
        if [[ "${live_count}" != "${canary_count}" ]]; then
            log "  MISMATCH ${t}: sampled ${live_count} from live, canary has ${canary_count} -- see ${err_log_dir}/${t}.err"
            mismatch=1
        fi
    done
    log "sample load complete over $(echo "${tables}" | wc -w) tables"
    [[ ${mismatch} -eq 0 ]] && rm -rf "${err_log_dir}"

    log "scanning canary postgres log for WARNING/ERROR/FATAL..."
    # Excludes the postgres image's own normal two-phase initdb startup
    # dance (a temporary instance starts, shuts down, then the real one
    # starts) -- "FATAL: the database system is shutting down" during that
    # window is expected on every fresh container, not a real issue.
    # Confirmed live 2026-09-19: false-positived the first real run.
    # "no usable system locales were found" (2026-09-19, first seen on the
    # nextcloud-db profile): well-documented Alpine/musl limitation on
    # postgres:*-alpine images generally, not something this update
    # introduced -- musl doesn't ship glibc's locale data, so initdb falls
    # back to the "C" locale. Harmless; does not affect data correctness.
    log_issues=$(podman logs "${CANARY_CONTAINER}" 2>&1 | grep -iE "WARNING|ERROR|FATAL" \
        | grep -v "database system was shut down\|starting PostgreSQL\|listening on\|database system is shutting down\|received fast shutdown request\|aborting any active transactions\|enabling \"trust\" authentication for local connections\|no usable system locales were found" || true)
    if [[ -n "${log_issues}" ]]; then
        log "log issues found:"
        echo "${log_issues}"
        fail=1
    fi

    if [[ ${mismatch} -eq 1 || ${fail} -eq 1 ]]; then
        log "RESULT: FAIL -- canary left running for inspection (podman logs/exec ${CANARY_CONTAINER}). Run 'cleanup' when done."
        exit 1
    fi

    log "RESULT: PASS -- new image ${new_digest} validated clean against live schema + a ${SAMPLE_LIMIT}-row/table real sample."
    log "Cleaning up canary (validation data only, nothing live was touched)..."
    cleanup_canary
    log "Next step, only on explicit operator confirmation: scripts/safe-pg-image-update.sh apply"
}

cmd_apply() {
    live_digest=$(podman inspect "${LIVE_CONTAINER}" --format '{{.Image}}' 2>/dev/null) \
        || die "live container ${LIVE_CONTAINER} not found/running"
    new_digest=$(podman image inspect "${IMAGE}" --format '{{.Id}}' 2>/dev/null) \
        || die "image not pulled locally -- run 'test' first"
    if [[ "${live_digest}" == "${new_digest}" ]]; then
        log "live container already runs this image digest -- nothing to apply."
        exit 0
    fi
    log "applying: restarting ${LIVE_SERVICE} to pick up the already-pulled, already-validated image."
    log "data volume is untouched -- same Postgres major version, in-place restart only."
    systemctl --user restart "${LIVE_SERVICE}" || die "restart failed"
    for _ in $(seq 1 30); do
        if podman exec -e PGPASSWORD="${LIVE_PW}" "${LIVE_CONTAINER}" pg_isready -U "${PG_USER}" -d "${PG_DB}" >/dev/null 2>&1; then
            log "live container back up and accepting connections: $(podman exec -e PGPASSWORD="${LIVE_PW}" "${LIVE_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -tAc 'SELECT version();')"
            exit 0
        fi
        sleep 2
    done
    die "live container did not become ready after restart -- check journalctl --user -u ${LIVE_SERVICE}"
}

case "${1:-}" in
    test) cmd_test ;;
    apply) cmd_apply ;;
    cleanup) cleanup_canary ;;
    *) echo "Usage: $0 {test|apply|cleanup}" >&2; exit 2 ;;
esac
