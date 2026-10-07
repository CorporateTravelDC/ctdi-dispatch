#!/bin/bash
# scripts/unit-failure-notify.sh
#
# 2026-08-26: generic OnFailure= handler for weekly headless-claude timer
# units (docs-drift-weekly, second-brain-weekly-dump). Found by an
# automated doc-verify pass: both share a real failure mode -- a
# Sunday-morning fire that lands during an exhausted Claude usage window
# exits non-zero with no retry, and since these only run weekly, a silent
# failure sits in `--failed` for up to 7 days before anyone notices. This
# doesn't fix the underlying retry gap (that needs real scheduling logic,
# not a notification), but it closes the "silent for a week" half by
# pinging ops the moment `systemctl --user` marks the unit failed.
#
# Usage: unit-failure-notify.sh <unit-name>
# Wired via OnFailure=corporatetraveldc-unit-failure-notify@%n.service on
# the units it protects.
set -uo pipefail
# 2026-10-05 (argv-token sweep): a bearer token never goes on a command line --
# /proc/<pid>/cmdline is world-readable here (no hidepid), i.e. readable by
# every team account. authhdr NAME TOKEN puts the header on a private fd and
# sets NAME=(-H @/dev/fd/N) for ONE curl call (re-run it before each call).
authhdr() { local -n _ah="$1"; [[ -n "${_AUTHHDR_FD:-}" ]] && exec {_AUTHHDR_FD}<&-; exec {_AUTHHDR_FD}<<<"Authorization: Bearer $2"; _ah=(-H "@/dev/fd/${_AUTHHDR_FD}"); }

UNIT="${1:-unknown-unit}"

ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

read_env_var() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    grep -m1 "^${key}=" "${file}" 2>/dev/null | cut -d'=' -f2-
}

NTFY_BASE="$(read_env_var NTFY_BASE_URL "${ENV_FILE}")"
NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_OPS="$(read_env_var NTFY_OPS_TOPIC "${ENV_FILE}")"
NTFY_OPS="${NTFY_OPS:-ops-health}"
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "${SECRETS_FILE}")"
NTFY_TOKEN="${NTFY_TOKEN%%:*}"

auth_args=()
[[ -n "${NTFY_TOKEN}" ]] && authhdr auth_args "${NTFY_TOKEN}"

curl -sf --max-time 5 \
    "${auth_args[@]}" \
    -H "Title: ${UNIT} failed" \
    -H "Priority: 4" \
    -H "Tags: warning" \
    -d "systemctl --user is marking ${UNIT} as failed. This unit only runs weekly -- check it now rather than waiting for the next scheduled fire. journalctl --user -u ${UNIT} for detail." \
    "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1

exit 0
