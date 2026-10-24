#!/bin/bash
# scripts/second-brain-dump-staleness.sh -- alert ops-health when the weekly second-brain dump
# (scripts/second-brain-weekly-dump.sh) has not written a log for more than 9 days.
#
# 2026-10-22 (operator): from 2026-09-21 the weekly dump timer fired outside the maintenance window
# and every run was skipped -- silently, for a month. Nothing watched the result. This check reads
# the newest *.log in the dump's LOG_DIR (one per run, named by date) and, when it is older than
# DUMP_MAX_AGE_D days (default 9 = one missed week plus slack) or there is none at all, sends one
# ntfy to the ops topic (same ntfy_send / token-on-a-private-fd pattern as the dump script) and
# exits 1, so a unit running it also fails visibly (OnFailure).
#
#   second-brain-dump-staleness.sh [--no-notify]     exit 0 fresh, 1 stale/missing
# Env: DUMP_LOG_DIR (default /var/lib/corporatetraveldc/second-brain-weekly-dump), DUMP_MAX_AGE_D (9).
# Needs a timer to run it (e.g. daily); none is installed by this script. ASCII only.
set -uo pipefail
# 2026-10-05 (argv-token sweep): a bearer token never goes on a command line (see the dump script).
authhdr() { local -n _ah="$1"; [[ -n "${_AUTHHDR_FD:-}" ]] && exec {_AUTHHDR_FD}<&-; exec {_AUTHHDR_FD}<<<"Authorization: Bearer $2"; _ah=(-H "@/dev/fd/${_AUTHHDR_FD}"); }

NOTIFY=1; [[ "${1:-}" == --no-notify ]] && NOTIFY=0
DUMP_LOG_DIR="${DUMP_LOG_DIR:-/var/lib/corporatetraveldc/second-brain-weekly-dump}"
DUMP_MAX_AGE_D="${DUMP_MAX_AGE_D:-9}"
[[ "$DUMP_MAX_AGE_D" =~ ^[0-9]+$ ]] || { echo "DUMP_MAX_AGE_D must be whole days" >&2; exit 2; }

ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"
read_env_var() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    grep -m1 "^${key}=" "${file}" 2>/dev/null | cut -d'=' -f2-
}

ntfy_send() {
    local title="$1" msg="$2" priority="${3:-3}"
    local base ops token auth_args=()
    base="$(read_env_var NTFY_BASE_URL "${ENV_FILE}")"; base="${base:-http://127.0.0.1:2586}"
    ops="$(read_env_var NTFY_OPS_TOPIC "${ENV_FILE}")"; ops="${ops:-ops-health}"
    token="$(read_env_var NTFY_TOKEN "${SECRETS_FILE}")"; token="${token%%:*}"
    [[ -n "${token}" ]] && authhdr auth_args "${token}"
    curl -sf --max-time 5 \
        "${auth_args[@]}" \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: brain,warning" \
        -d "${msg}" \
        "${base}/${ops}" >/dev/null 2>&1
}

now=$(date +%s)
newest=""; newest_m=0
if [[ -d "$DUMP_LOG_DIR" ]]; then
    for f in "$DUMP_LOG_DIR"/*.log; do
        [[ -f "$f" ]] || continue
        m=$(stat -c %Y "$f" 2>/dev/null) || continue
        (( m > newest_m )) && { newest_m=$m; newest=$f; }
    done
fi

if [[ -z "$newest" ]]; then
    msg="no weekly-dump log in ${DUMP_LOG_DIR} at all -- the weekly second-brain dump has never run here or its logs are gone. Check: systemctl --user list-timers corporatetraveldc-second-brain-weekly-dump.timer; journalctl --user -u corporatetraveldc-second-brain-weekly-dump.service"
else
    age_s=$(( now - newest_m )); age_d=$(( age_s / 86400 ))
    if (( age_s <= DUMP_MAX_AGE_D * 86400 )); then
        echo "second-brain weekly dump fresh: newest log $(basename "$newest") is ${age_d} day(s) old (limit ${DUMP_MAX_AGE_D})"
        exit 0
    fi
    msg="newest weekly-dump log $(basename "$newest") is ${age_d} days old (limit ${DUMP_MAX_AGE_D}) -- runs are being skipped (2026-09-21..10-22: fired outside the maintenance window). Check: systemctl --user list-timers corporatetraveldc-second-brain-weekly-dump.timer; journalctl --user -u corporatetraveldc-second-brain-weekly-dump.service"
fi
echo "STALE: $msg"
if (( NOTIFY )); then
    ntfy_send "Second-brain weekly dump is stale" "$msg" 4 || echo "ntfy send failed (alert not delivered)" >&2
fi
exit 1
