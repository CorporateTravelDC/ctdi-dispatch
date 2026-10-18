#!/usr/bin/env bash
# scripts/demo-isolation-check.sh -- LIVE demo/production database isolation
# check (2026-10-16, demo/production split, relay 1). Called by
# scripts/scheduled-integrity-sweep.sh every 15 min; runnable by hand.
#
# What it checks, against the running system (not the tracked files -- the
# tests cover those):
#   1. every RUNNING demo container (corporatetraveldc-demo-api,
#      corporatetraveldc-runner-demo, or any container whose environment
#      carries DISPATCH_ROLE=demo) mounts neither the production Postgres
#      socket volume (corporatetraveldc-pgsql-sock) nor the live state tree
#      (/var/lib/corporatetraveldc) nor the scoped secret directory
#      (/etc/corporatetraveldc/svc), and its environment carries no
#      DISPATCH_PG_PASSWORD. Only env NAMES are inspected; no value is ever
#      printed.
#   2. the PRODUCTION instance has zero pg_stat_activity rows for the demo
#      application names and no `demo` / `demo_owner` role.
#   3. the DEMO instance (when running) has no superuser but demo_owner, no
#      `dispatch` role, and no table outside the six demo tables (+
#      schema_migrations, the runner's bookkeeping).
# Exit 0 clean, 1 any finding (one line per finding on stdout), 3 when
# podman is unusable. A demo instance that is not running is reported, not
# failed (relay 1 is additive; nothing demo-facing runs on it yet).
#
# Usage: scripts/demo-isolation-check.sh [--quiet]
set -uo pipefail

PROD_PG="${PROD_PG:-corporatetraveldc-pgsql}"
DEMO_PG="${DEMO_PG:-corporatetraveldc-pgsql-demo}"
DEMO_APP_NAMES="corporatetraveldc-demo-api corporatetraveldc-runner-demo"
PROD_SOCK_VOLUME="corporatetraveldc-pgsql-sock"
DEMO_TABLES="snapshots brief_archive meta demo_profiles chat_messages scrub_ledger schema_migrations"
QUIET=0; [[ "${1:-}" == "--quiet" ]] && QUIET=1
FINDINGS=0

finding() { FINDINGS=$((FINDINGS + 1)); echo "[DEMO-ISOLATION] FAIL: $*"; }
ok() { [[ ${QUIET} -eq 1 ]] || echo "[DEMO-ISOLATION] ok: $*"; }
info() { [[ ${QUIET} -eq 1 ]] || echo "[DEMO-ISOLATION] info: $*"; }

command -v podman >/dev/null 2>&1 || { echo "[DEMO-ISOLATION] podman not available"; exit 3; }

running=$(podman ps --format '{{.Names}}' 2>/dev/null) || { echo "[DEMO-ISOLATION] podman ps failed"; exit 3; }

# psql inside a Postgres container with its OWN environment (the credential
# never touches this shell, the same pattern scripts/backup/ctdi-backup.sh uses).
psql_in() {   # container, sql -> stdout (rows, -At)
    podman exec -i "$1" sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"' sh "$2" </dev/null 2>/dev/null
}

is_demo_container() {   # name -> 0 if it is a demo app container
    local n="$1" base="${1#systemd-}"
    for d in ${DEMO_APP_NAMES}; do [[ "${base}" == "${d}" ]] && return 0; done
    podman inspect "${n}" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep -qx 'DISPATCH_ROLE=demo'
}

# -- 1. running demo containers: mounts + env names ---------------------------
demo_seen=0
for c in ${running}; do
    is_demo_container "${c}" || continue
    demo_seen=$((demo_seen + 1))
    before=${FINDINGS}
    # three columns always: name-or-dash, source, destination (a bind mount has no name)
    mounts=$(podman inspect "${c}" --format '{{range .Mounts}}{{println (or .Name "-") " " .Source " " .Destination}}{{end}}' 2>/dev/null)
    if grep -q "${PROD_SOCK_VOLUME}" <<<"${mounts}"; then
        finding "${c} mounts the production Postgres socket volume ${PROD_SOCK_VOLUME}"
    fi
    if awk '{print $2}' <<<"${mounts}" | grep -qE '^/var/lib/corporatetraveldc(/|$)'; then
        finding "${c} mounts the live state tree /var/lib/corporatetraveldc"
    fi
    if awk '{print $2}' <<<"${mounts}" | grep -qE '^/etc/corporatetraveldc/svc(/|$)'; then
        finding "${c} mounts the scoped secret directory /etc/corporatetraveldc/svc"
    fi
    env_names=$(podman inspect "${c}" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | cut -d= -f1)
    if grep -qx 'DISPATCH_PG_PASSWORD' <<<"${env_names}"; then
        finding "${c} carries DISPATCH_PG_PASSWORD in its environment (the production credential)"
    fi
    [[ ${FINDINGS} -eq ${before} ]] && ok "${c}: no production socket, no live tree, no svc dir, no production password name"
done
[[ ${demo_seen} -gt 0 ]] || info "no demo app container running"

# -- 2. production instance: no demo sessions, no demo roles ------------------
if grep -qx "${PROD_PG}" <<<"${running}"; then
    sessions=$(psql_in "${PROD_PG}" "SELECT count(*) FROM pg_stat_activity WHERE application_name IN ('corporatetraveldc-demo-api', 'corporatetraveldc-runner-demo', 'corporatetraveldc-demo-api.service', 'corporatetraveldc-runner-demo.service')")
    if [[ -z "${sessions}" ]]; then
        finding "${PROD_PG}: could not read pg_stat_activity"
    elif [[ "${sessions}" != "0" ]]; then
        finding "${PROD_PG}: ${sessions} session(s) from a demo application name on the PRODUCTION instance"
    else
        ok "${PROD_PG}: no demo application sessions"
    fi
    roles=$(psql_in "${PROD_PG}" "SELECT count(*) FROM pg_roles WHERE rolname IN ('demo', 'demo_owner')")
    if [[ -z "${roles}" ]]; then
        finding "${PROD_PG}: could not read pg_roles"
    elif [[ "${roles}" != "0" ]]; then
        finding "${PROD_PG}: a demo role exists on the PRODUCTION instance"
    else
        ok "${PROD_PG}: no demo roles"
    fi
else
    finding "${PROD_PG} is not running"
fi

# -- 3. demo instance: owner only, no dispatch, six tables --------------------
if grep -qx "${DEMO_PG}" <<<"${running}"; then
    supers=$(psql_in "${DEMO_PG}" "SELECT string_agg(rolname, ',') FROM pg_roles WHERE rolsuper AND rolname <> 'demo_owner'")
    [[ -n "${supers}" ]] && finding "${DEMO_PG}: superuser role(s) other than demo_owner: ${supers}"
    dispatch=$(psql_in "${DEMO_PG}" "SELECT count(*) FROM pg_roles WHERE rolname = 'dispatch'")
    if [[ -z "${dispatch}" ]]; then
        finding "${DEMO_PG}: could not read pg_roles"
    elif [[ "${dispatch}" != "0" ]]; then
        finding "${DEMO_PG}: a 'dispatch' role exists on the demo instance"
    fi
    tables=$(psql_in "${DEMO_PG}" "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
    extra=""
    for t in ${tables}; do
        grep -qw "${t}" <<<"${DEMO_TABLES}" || extra="${extra} ${t}"
    done
    [[ -n "${extra}" ]] && finding "${DEMO_PG}: table(s) outside the demo set:${extra}"
    [[ -n "${supers}" || -n "${extra}" ]] || ok "${DEMO_PG}: demo_owner is the only superuser, no dispatch role, tables within the demo set"
else
    info "${DEMO_PG} is not running (relay 1: additive, not yet started)"
fi

if [[ ${FINDINGS} -eq 0 ]]; then
    [[ ${QUIET} -eq 1 ]] || echo "[DEMO-ISOLATION] clean"
    exit 0
fi
echo "[DEMO-ISOLATION] ${FINDINGS} finding(s)"
exit 1
