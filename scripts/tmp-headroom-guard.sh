#!/bin/bash
# scripts/tmp-headroom-guard.sh -- keep at least TMP_MIN_FREE_PCT (default 10%)
# of /tmp free, always (operator 2026-10-14).
#
# Why: /tmp is an 8 GB tmpfs. On 2026-10-14 ~23:15Z it reached 100%: 6,005
# SQLite test databases (6.4 GB) leaked by about 20 tests since 10-06. With no
# space, GPG could not verify signatures and the integrity sweep's build
# provenance failed, tests errored with "database or disk is full", and the
# shell could not write. Logs, the audit chain, the guard worktree's patch
# step, mktemp in every relay and pytest all need /tmp. Same idea as the podman
# prune guard: reclaim what is provably ours and stale, in a fixed order,
# before the floor is reached -- never anything recent, never another user's.
#
# Reclaim order (each step only while free < floor), all owned by THIS user:
#   1. leaked test databases  /tmp/tmpXXXXXXXX.db{,-wal,-shm}   older than 30 min
#   2. pytest base dirs        /tmp/pytest-of-$USER/pytest-N     all but the newest 3, older than 6 h
#   3. stale mktemp entries    /tmp/tmp.XXXXXXXXXX               older than 24 h
# Never touched: other users' files, sockets, /tmp/claude-* (live session
# scratch), anything newer than its age floor. If free is still under the
# floor afterwards, one ops-health alert per distinct problem (the key is the
# list of top consumers, never sizes or times), cleared when healthy.
#
#   tmp-headroom-guard.sh            check, reclaim if under the floor, alert if still under
#   tmp-headroom-guard.sh --dry-run  report what would be reclaimed, change nothing
#   tmp-headroom-guard.sh --status   print usage and the floor
set -uo pipefail
authhdr() { local -n _ah="$1"; [[ -n "${_AUTHHDR_FD:-}" ]] && exec {_AUTHHDR_FD}<&-; exec {_AUTHHDR_FD}<<<"Authorization: Bearer $2"; _ah=(-H "@/dev/fd/${_AUTHHDR_FD}"); }

TMP_DIR="${TMP_GUARD_DIR:-/tmp}"
FLOOR_PCT="${TMP_MIN_FREE_PCT:-10}"
STATE_DIR="${TMP_GUARD_STATE_DIR:-/var/lib/corporatetraveldc/tmp-headroom-guard}"
LOG_FILE="${STATE_DIR}/guard.log"
ALERT_KEY_FILE="${STATE_DIR}/last-alert-key"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"
ME="$(id -un)"
MODE="run"
case "${1:-}" in --dry-run) MODE="dry-run" ;; --status) MODE="status" ;; esac
mkdir -p "${STATE_DIR}" 2>/dev/null || true

log() { local ts; ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ'); echo "[${ts}] $*" | tee -a "${LOG_FILE}" 2>/dev/null || echo "[${ts}] $*"; }
read_env_var() { [[ -f "$2" ]] || return 0; grep -m1 "^$1=" "$2" 2>/dev/null | cut -d'=' -f2-; }

free_pct() { df -P "${TMP_DIR}" | awk 'NR==2 { printf "%d", (100 * $4) / $2 }'; }
under_floor() { [[ "$(free_pct)" -lt "${FLOOR_PCT}" ]]; }

reclaim() {   # reclaim <label> <find args...>
    local label="$1"; shift
    local n bytes
    read -r n bytes < <(find "${TMP_DIR}" -maxdepth 1 -user "${ME}" "$@" -printf '%s\n' 2>/dev/null \
                        | awk '{ n++; s += $1 } END { printf "%d %d\n", n, s }')
    [[ "${n}" -gt 0 ]] || return 0
    if [[ "${MODE}" == "dry-run" ]]; then
        log "dry-run: would reclaim ${n} ${label} ($((bytes / 1048576)) MiB)"
    else
        find "${TMP_DIR}" -maxdepth 1 -user "${ME}" "$@" -delete 2>/dev/null
        log "reclaimed ${n} ${label} ($((bytes / 1048576)) MiB); free now $(free_pct)%"
    fi
}

reclaim_pytest_dirs() {
    local base="${TMP_DIR}/pytest-of-${ME}"
    [[ -d "${base}" ]] || return 0
    local old
    old=$(find "${base}" -mindepth 1 -maxdepth 1 -type d -name 'pytest-[0-9]*' -mmin +360 -printf '%T@ %p\n' 2>/dev/null \
          | sort -rn | tail -n +4 | cut -d' ' -f2-)
    [[ -n "${old}" ]] || return 0
    if [[ "${MODE}" == "dry-run" ]]; then
        log "dry-run: would reclaim $(wc -l <<<"${old}") old pytest base dir(s)"
    else
        xargs -r -d '\n' rm -rf -- <<<"${old}"
        log "reclaimed $(wc -l <<<"${old}") old pytest base dir(s); free now $(free_pct)%"
    fi
}

reclaim_mktemp() {   # mktemp files AND dirs (relay patches, manifest scratch) older than 24 h
    local old
    old=$(find "${TMP_DIR}" -mindepth 1 -maxdepth 1 -user "${ME}" -name 'tmp.??????????' -mmin +1440 2>/dev/null)
    [[ -n "${old}" ]] || return 0
    if [[ "${MODE}" == "dry-run" ]]; then
        log "dry-run: would reclaim $(wc -l <<<"${old}") stale mktemp entr(ies)"
    else
        xargs -r -d '\n' rm -rf -- <<<"${old}"
        log "reclaimed $(wc -l <<<"${old}") stale mktemp entr(ies); free now $(free_pct)%"
    fi
}

top_consumers() {   # names only, for the alert body and its key
    du -xs "${TMP_DIR}"/* "${TMP_DIR}"/.[!.]* 2>/dev/null | sort -rn | head -5 | awk '{ print $2 }' | xargs -r -n1 basename
}

ntfy_once() {   # one alert per distinct problem; key = consumer names only
    local key="$1" title="$2" msg="$3"
    [[ "$(cat "${ALERT_KEY_FILE}" 2>/dev/null)" == "${key}" ]] && { log "alert unchanged, not re-sent"; return 0; }
    local base topic token auth_args=()
    base="${TMP_GUARD_NTFY_BASE:-$(read_env_var NTFY_BASE_URL "${ENV_FILE}")}"; base="${base:-http://127.0.0.1:2586}"
    topic="$(read_env_var NTFY_OPS_TOPIC "${ENV_FILE}")"; topic="${topic:-ops-health}"
    token="$(read_env_var NTFY_TOKEN "${SECRETS_FILE}")"; token="${token%%:*}"
    [[ -n "${token}" ]] && authhdr auth_args "${token}"
    if curl -sf --max-time 5 "${auth_args[@]}" -H "Title: ${title}" -H "Priority: 4" -H "Tags: floppy_disk" \
            -d "${msg}" "${base}/${topic}" >/dev/null 2>&1; then
        echo "${key}" > "${ALERT_KEY_FILE}"
    else
        log "warn: ntfy send failed (token_set=$([[ -n "${token}" ]] && echo yes || echo no))"
    fi
}

if [[ "${MODE}" == "status" ]]; then
    echo "/tmp free $(free_pct)% (floor ${FLOOR_PCT}%)"; df -h "${TMP_DIR}" | tail -1; exit 0
fi

start="$(free_pct)"
if ! under_floor && [[ "${MODE}" != "dry-run" ]]; then
    [[ -f "${ALERT_KEY_FILE}" ]] && { rm -f "${ALERT_KEY_FILE}"; log "healthy again: free ${start}% (alert state cleared)"; }
    exit 0
fi
log "free ${start}% (floor ${FLOOR_PCT}%) -- reclaiming"
under_floor || [[ "${MODE}" == "dry-run" ]] && reclaim "leaked test database file(s)" -type f -regextype posix-extended \
    -regex "${TMP_DIR}/tmp[a-z0-9_]{8}\.db(-wal|-shm)?" -mmin +30
under_floor || [[ "${MODE}" == "dry-run" ]] && reclaim_pytest_dirs
under_floor || [[ "${MODE}" == "dry-run" ]] && reclaim_mktemp
[[ "${MODE}" == "dry-run" ]] && exit 0
if under_floor; then
    consumers="$(top_consumers | paste -sd, -)"
    log "STILL UNDER FLOOR: free $(free_pct)% after reclaim; top consumers: ${consumers}"
    ntfy_once "${consumers}" "/tmp below ${FLOOR_PCT}% free" \
        "/tmp is $(free_pct)% free (floor ${FLOOR_PCT}%) after reclaiming stale test DBs, pytest dirs and mktemp entries. Largest entries: ${consumers}. GPG checks, relays, tests and logs need /tmp -- free space before the next relay."
    exit 1
fi
rm -f "${ALERT_KEY_FILE}"
exit 0
