#!/usr/bin/env bash
# scripts/maintenance-window-guard.sh
#
# Gate for long-running report-tier jobs: exit 0 only when the current local
# time falls inside the overnight maintenance window, non-zero otherwise.
#
# Wired into governed units as `ExecCondition=` (NOT ExecStartPre=). That
# distinction is deliberate and load-bearing: a non-zero ExecCondition makes
# systemd SKIP the unit cleanly, leaving it `inactive (dead)`. A non-zero
# ExecStartPre marks the unit `failed`, which on this box fires
# OnFailure=corporatetraveldc-unit-failure-notify@ -- i.e. an ntfy page to the
# operator every single night the guard did its job. Requires systemd 243+;
# this box runs 259.
#
# WHY THE WINDOW EXISTS
# Long report-tier jobs (second-brain compiles, weekly summaries, the daily
# watch family) contend for the same two cores as the latency-sensitive
# flight/weather alert path. Missing one overnight alert cycle costs far less
# than clobbering a brief the operator is waiting on, so the long jobs are
# confined to the quiet hours. See docs/ and the timer unit descriptions.
#
# *** FOOTGUN -- READ BEFORE ADDING THIS TO A UNIT ***
# Attaching this guard to a unit whose OnCalendar= falls OUTSIDE the window
# permanently disables that unit. It will skip, silently and forever, with no
# failure and no alert. Verify the unit's schedule is fully inside the window
# BEFORE wiring the guard. Units that fire multiple times per day (e.g.
# `00,06,12,18:02:00`) must NOT be guarded -- the guard would kill every slot
# but the overnight one.
#
# Usage:
#   maintenance-window-guard.sh            # silent; exit 0 = inside, 1 = outside
#   maintenance-window-guard.sh --check    # human-readable dry run, same exit code
set -uo pipefail

CONFIG_FILE="${CTDC_CONFIG_FILE:-/etc/corporatetraveldc/dispatch.env}"
# Remember any values set explicitly in the environment BEFORE sourcing the
# config, so an inline override (CTDC_MAINTENANCE_WINDOW_START=02:00 guard.sh
# --check) beats the deployment config rather than being clobbered by it.
# Without this, --check could only ever test the live window, which makes the
# midnight-wrap logic impossible to verify at any hour but the real boundary.
_env_start="${CTDC_MAINTENANCE_WINDOW_START:-}"
_env_end="${CTDC_MAINTENANCE_WINDOW_END:-}"
_env_tz="${CTDC_MAINTENANCE_WINDOW_TZ:-}"

# Sourced here rather than relying on the unit's EnvironmentFile=: for Quadlet
# .container units, EnvironmentFile= is handed to the CONTAINER, while
# ExecCondition= runs on the HOST and would never see it.
# 2026-10-04: was `set -a; source "${CONFIG_FILE}"`. The env files are
# deliberately unquoted (podman --env-file semantics) and bash word-splits
# values with spaces (AMTRAK_CORE_ROUTES) -- every guarded unit was logging
# "command not found" from this line; for the secrets file the same pattern
# leaked a credential fragment. Read the three keys literally instead.
_cfg() { grep -m1 "^$1=" "${CONFIG_FILE}" 2>/dev/null | cut -d= -f2-; }
if [[ -r "${CONFIG_FILE}" ]]; then
    CTDC_MAINTENANCE_WINDOW_START="$(_cfg CTDC_MAINTENANCE_WINDOW_START)"
    CTDC_MAINTENANCE_WINDOW_END="$(_cfg CTDC_MAINTENANCE_WINDOW_END)"
    CTDC_MAINTENANCE_WINDOW_TZ="$(_cfg CTDC_MAINTENANCE_WINDOW_TZ)"
fi

[[ -n "${_env_start}" ]] && CTDC_MAINTENANCE_WINDOW_START="${_env_start}"
[[ -n "${_env_end}"   ]] && CTDC_MAINTENANCE_WINDOW_END="${_env_end}"
[[ -n "${_env_tz}"    ]] && CTDC_MAINTENANCE_WINDOW_TZ="${_env_tz}"

WINDOW_START="${CTDC_MAINTENANCE_WINDOW_START:-23:00}"
WINDOW_END="${CTDC_MAINTENANCE_WINDOW_END:-05:00}"
# Pinned to the same zone the governed units declare in OnCalendar=, so the
# guard cannot disagree with the scheduler if the host TZ ever drifts.
WINDOW_TZ="${CTDC_MAINTENANCE_WINDOW_TZ:-America/New_York}"

CHECK_MODE=0
AT_OVERRIDE=""
WEEKDAY_OVERRIDE=""
ROLLING=0
# --rolling (2026-10-04, operator directive "make the maintenance window a
# rolling time ... three or four candidates throughout the day that are
# operationally aware"): instead of the fixed START-END, the guard is open
# inside any of today's windows drawn by scripts/quiet-window-report.py from
# the rolling 30-day load profile (/var/lib/corporatetraveldc/reports/
# quiet-windows.json, "today_windows"). Falls back to the fixed window when
# the profile is missing or older than 36h, so nothing can be stranded. Only
# queue-dispatched units use --rolling (scripts/maintenance-dispatch.sh);
# timer-driven units keep the fixed window -- see the FOOTGUN above.
QUIET_JSON="${CTDC_QUIET_WINDOWS_JSON:-/var/lib/corporatetraveldc/reports/quiet-windows.json}"
while (( $# )); do
    case "$1" in
        --rolling) ROLLING=1 ;;
        --check|--dry-run) CHECK_MODE=1 ;;
        # --at HH:MM evaluates the window against a hypothetical clock instead
        # of the real one. Exists so the FOOTGUN above is checkable before
        # wiring a unit: `--check --at 04:30` answers "would a 04:30 job run?"
        # without waiting until 04:30. Dry-run only.
        --at) shift; AT_OVERRIDE="${1:-}"; CHECK_MODE=1 ;;
        # --weekday Mon..Sun pairs with --at for testing the rolling lookup at
        # any day/time without waiting for it. Dry-run only.
        --weekday) shift; WEEKDAY_OVERRIDE="${1:-}"; CHECK_MODE=1 ;;
        *) echo "maintenance-window-guard: unknown argument '$1'" >&2; exit 2 ;;
    esac
    shift
done

# HH:MM -> minutes since midnight. Rejects anything malformed rather than
# silently coercing to 0, which would make the window start at midnight.
to_minutes() {
    local hhmm="$1" h m
    if [[ ! "${hhmm}" =~ ^([0-9]{1,2}):([0-9]{2})$ ]]; then
        echo "maintenance-window-guard: malformed time '${hhmm}' (want HH:MM)" >&2
        return 1
    fi
    h="${BASH_REMATCH[1]}"; m="${BASH_REMATCH[2]}"
    # Strip leading zeros so bash doesn't read them as octal.
    h=$((10#${h})); m=$((10#${m}))
    if (( h > 23 || m > 59 )); then
        echo "maintenance-window-guard: out-of-range time '${hhmm}'" >&2
        return 1
    fi
    echo $(( h * 60 + m ))
}

start_min="$(to_minutes "${WINDOW_START}")" || exit 2
end_min="$(to_minutes "${WINDOW_END}")"     || exit 2

if [[ -n "${AT_OVERRIDE}" ]]; then
    now_hhmm="${AT_OVERRIDE}"
else
    now_hhmm="$(TZ="${WINDOW_TZ}" date +%H:%M)"
fi
now_min="$(to_minutes "${now_hhmm}")" || exit 2

inside=1
wrap="no"
rolling_note=""
# 2026-10-04 (duel M1): look the windows up by the CURRENT local weekday at
# check time from maintenance_candidates -- NOT today_windows, which the
# report only rewrites at 05:30, so before then the guard applied yesterday's
# list and every window starting before 05:30 (Monday 03:00, the quietest
# slot in the profile) could never open. A window that wraps past midnight
# belongs to the weekday it STARTED on: at 00:30 Tuesday, Monday's 23:00+2h
# window is open; Tuesday's own 23:00 window is not. The candidates are a
# per-weekday weekly profile, so a profile up to 8 days old is still valid.
if (( ROLLING )) && [[ -r "${QUIET_JSON}" ]] && (( $(date +%s) - $(stat -c %Y "${QUIET_JSON}") < 8*86400 )); then
    _wd="${WEEKDAY_OVERRIDE:-$(TZ="${WINDOW_TZ}" date +%a)}"
    _res="$(python3 - "${QUIET_JSON}" "${_wd}" "${now_min}" <<'PYEOF'
import json, sys
path, wd, now = sys.argv[1], sys.argv[2], int(sys.argv[3])
days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
try:
    cands = json.load(open(path)).get("maintenance_candidates") or {}
except Exception:
    cands = {}
if wd not in days or not cands:
    print("none"); sys.exit(0)
prev = days[(days.index(wd) - 1) % 7]
inside, notes = 1, []
for w in cands.get(wd, []):                       # today's windows, same-day part
    s = int(w["start_hour"]) * 60; e = s + int(w.get("width_h", 2)) * 60
    notes.append(f"{s//60:02d}:{s%60:02d}+{(e-s)//60}h")
    if s <= now < min(e, 1440): inside = 0
for w in cands.get(prev, []):                     # yesterday's windows spilling past midnight
    s = int(w["start_hour"]) * 60; e = s + int(w.get("width_h", 2)) * 60
    if e > 1440:
        notes.append(f"({prev} {s//60:02d}:{s%60:02d} spill to {(e-1440)//60:02d}:{(e-1440)%60:02d})")
        if now < e - 1440: inside = 0
print(f"{inside} {wd} " + " ".join(notes))
PYEOF
)"
    if [[ "${_res}" != none && -n "${_res}" ]]; then
        inside="${_res%% *}"; _rest="${_res#* }"
        wrap="rolling (weekday ${_rest%% *} from ${QUIET_JSON##*/})"
        (( CHECK_MODE )) && printf 'rolling     : %s\n' "${_rest#* }"
    else
        ROLLING=0; wrap="rolling requested but no maintenance_candidates -> fixed fallback"
    fi
elif (( ROLLING )); then
    ROLLING=0; wrap="rolling requested but profile missing/stale -> fixed fallback"
fi
if (( ROLLING )); then
    :   # decided above
elif (( start_min == end_min )); then
    # Degenerate: start == end. Treated as "always open" so a misconfiguration
    # can never silently disable every governed job.
    inside=0
    wrap="degenerate (start == end -> always open)"
elif (( start_min < end_min )); then
    # Same-day window, e.g. 01:00-05:00.
    (( now_min >= start_min && now_min < end_min )) && inside=0
    wrap="no"
else
    # Wraps midnight, e.g. 23:00-05:00: inside if at/after start OR before end.
    (( now_min >= start_min || now_min < end_min )) && inside=0
    wrap="yes"
fi

if (( CHECK_MODE )); then
    printf 'window      : %s-%s %s (wraps midnight: %s)\n' \
        "${WINDOW_START}" "${WINDOW_END}" "${WINDOW_TZ}" "${wrap}"
    printf 'now         : %s (%d min past midnight)\n' "${now_hhmm}" "${now_min}"
    printf 'bounds      : start=%d end=%d\n' "${start_min}" "${end_min}"
    if (( inside == 0 )); then
        printf 'result      : INSIDE window -- guarded units WOULD run (exit 0)\n'
    else
        printf 'result      : OUTSIDE window -- guarded units would be SKIPPED (exit 1)\n'
    fi
fi

exit "${inside}"
