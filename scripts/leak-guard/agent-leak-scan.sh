#!/usr/bin/env bash
# scripts/leak-guard/agent-leak-scan.sh -- the hourly ROOT redact + scan of every agent
# account's transcripts (corporatetraveldc-agent-leak-scan.service), with the LADD class
# REQUIRED (2026-10-20, freeze bug fix: the unit ran without the LADD class at all, so
# a listed identifier in an agent transcript was "NOT CHECKED" with exit 0 every hour).
#
# Installed by scripts/install-root-copies.sh next to transcript_leak_scan.py in
# /usr/local/libexec/ctdc; root never runs the checkout copy.
#
# Why not scripts/with-dispatch-env.sh like the operator-side callers: the installed copy
# has no src/ (root never imports operator-writable code) and root's python has no
# Postgres driver. The LADD union is piped, as push-public.sh's tripwire does, out of
# the operator's corporatetraveldc-pgsql container (runuser as the operator, rootless
# podman) straight into --ladd-stdin. Never written to disk, never printed. An empty or
# short list (container down, query failed) fails closed: exit 3, the unit fails.
#
# Exit: the first non-zero of redact, scan (1 = leak left, 3 = LADD not checked).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCAN="${HERE}/transcript_leak_scan.py"
OPERATOR="${CTDC_OPERATOR_ACCOUNT:-corporatetraveldc}"
LADD_SQL='SELECT n_number FROM faa_ladd_aircraft UNION SELECT n_number FROM faa_ladd_removals'

ladd_ids() {
    # Test hook: LEAK_GUARD_LADD_CMD replaces the database read (synthetic lists only).
    # Ignored as root: the unit always reads the real list.
    if [[ -n "${LEAK_GUARD_LADD_CMD:-}" && "${EUID}" -ne 0 ]]; then
        bash -c "${LEAK_GUARD_LADD_CMD}" </dev/null 2>/dev/null
        return
    fi
    local uid
    uid="$(id -u "${OPERATOR}")" || return 1
    runuser -u "${OPERATOR}" -- env XDG_RUNTIME_DIR="/run/user/${uid}" \
        podman exec corporatetraveldc-pgsql sh -c \
        "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -At -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"${LADD_SQL}\"" \
        </dev/null 2>/dev/null
}

rc=0
ladd_ids | nice -n 19 python3 "${SCAN}" redact --agents --require-ladd --ladd-stdin "$@" || rc=$?
src=0
ladd_ids | nice -n 19 python3 "${SCAN}" scan --agents --require-ladd --ladd-stdin "$@" || src=$?
(( rc != 0 )) && exit "${rc}"
exit "${src}"
