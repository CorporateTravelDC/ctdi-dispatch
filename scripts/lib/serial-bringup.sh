#!/bin/bash
# scripts/lib/serial-bringup.sh -- serialized, load-gated unit bring-up.
#
# The discipline scripts/serialized-rollout.sh proved live on 2026-09-06
# (31/31 containers, peak load 9.61, versus 40.28 -- and a LOCKDOWN trip --
# for the same stack restarted flat that morning), promoted into a library
# so boot (stack-boot-ctl.sh), the SWIM feed control surface
# (ingest-feed-ctl.sh) and the thermal guard's LOCKDOWN recovery
# (thermal-ingest-guard.py) all bring units up the same way. Work-order
# item 1, operator directive.
#
# Per unit: start/restart it, then sample load1 every SAMPLE_S seconds and
# do not touch the next unit until BOTH
#   (a) the unit's minimum window has elapsed, AND
#   (b) load1 has fallen below SERIAL_BRINGUP_LOAD_MAX,
# or the hard cap SERIAL_BRINGUP_MAX_WAIT_S is reached, in which case the
# sequence proceeds and logs a WARNING (never blocks forever -- a recovery
# that can hang is worse than one that runs a little hot). Baseline, peak
# (+offset) and end load are logged per unit in serialized-rollout.sh's
# one-line ">>> unit: baseline B -> peak P at +Ns -> end E  [state]" format.
# Elapsed time is counted from the sample loop, not the wall clock, so the
# discipline is exactly "N samples of SAMPLE_S" like the rollout script.
#
# Usage (sourced):
#   . scripts/lib/serial-bringup.sh
#   serial_bringup_unit restart corporatetraveldc-poller 180   # explicit window
#   serial_bringup_run  start   ntfy corporatetraveldc-poller:180 corporatetraveldc-web:90
# Usage (executed, e.g. from thermal-ingest-guard.py):
#   scripts/lib/serial-bringup.sh <start|restart> unit[:window_s] ...
# Unit names are given WITHOUT ".service"; an optional ":N" suffix sets
# that unit's minimum window in seconds (default SERIAL_BRINGUP_MIN_WINDOW_S).
#
# Env knobs (all optional):
#   SERIAL_BRINGUP_LOAD_MAX=12       load1 the next unit must be below
#   SERIAL_BRINGUP_MIN_WINDOW_S=60   default per-unit minimum window
#   SERIAL_BRINGUP_MAX_WAIT_S=300    hard cap per unit (pre-start hold and
#                                    post-start window each), then proceed
#   SERIAL_BRINGUP_SAMPLE_S=10       sample interval
#   SERIAL_BRINGUP_LOADAVG=/proc/loadavg   load source (test hook)
#   SERIAL_BRINGUP_SKIP_ACTIVE=1     "start" of an already-active unit is
#                                    logged and skipped without a window
#                                    (boot idempotency: a second orchestrator
#                                    finding everything up costs nothing)
#   SERIAL_BRINGUP_THERMAL=/sys/class/thermal/thermal_zone0/temp
#                                    CPU temp source, millidegrees (test hook)
#   SERIAL_BRINGUP_TEMP_WARN_C=79    OBSERVE-ONLY thermal monitor (operator
#                                    decision 2026-09-06): the temp is read
#                                    at every sample and logged next to the
#                                    load; at/above this value a WARNING
#                                    line is logged and the summary line is
#                                    tagged TEMP-WARN, but the sequence is
#                                    NEVER paused, aborted or reordered on
#                                    temperature. While a serialized restore
#                                    runs inside thermal-ingest-guard.py the
#                                    guard's own timer cannot fire, so this
#                                    is the only thermal reading taken for
#                                    the duration -- it exists so the
#                                    journal shows what the box did, not to
#                                    act. 79 = the guard's tier-2 default
#                                    (THERMAL_GUARD_TIER2_TEMP_C); the guard
#                                    forwards that value when it calls us.
#
# ASCII only (repo convention). Safe under set -u; no set -e assumed.

SERIAL_BRINGUP_LOAD_MAX=${SERIAL_BRINGUP_LOAD_MAX:-12}
SERIAL_BRINGUP_MIN_WINDOW_S=${SERIAL_BRINGUP_MIN_WINDOW_S:-60}
SERIAL_BRINGUP_MAX_WAIT_S=${SERIAL_BRINGUP_MAX_WAIT_S:-300}
SERIAL_BRINGUP_SAMPLE_S=${SERIAL_BRINGUP_SAMPLE_S:-10}
SERIAL_BRINGUP_LOADAVG=${SERIAL_BRINGUP_LOADAVG:-/proc/loadavg}
SERIAL_BRINGUP_SKIP_ACTIVE=${SERIAL_BRINGUP_SKIP_ACTIVE:-1}
SERIAL_BRINGUP_THERMAL=${SERIAL_BRINGUP_THERMAL:-/sys/class/thermal/thermal_zone0/temp}
SERIAL_BRINGUP_TEMP_WARN_C=${SERIAL_BRINGUP_TEMP_WARN_C:-79}

serial_bringup_load() { cut -d' ' -f1 "${SERIAL_BRINGUP_LOADAVG}" 2>/dev/null || echo 0; }
# CPU temp in whole degrees C, or "-" if the sensor is unreadable (a VM, a
# test box): observe-only, so a missing sensor is never an error.
serial_bringup_temp() {
    local mc
    mc=$(cat "${SERIAL_BRINGUP_THERMAL}" 2>/dev/null)
    if [[ "${mc}" =~ ^[0-9]+$ ]]; then echo $((mc / 1000)); else echo "-"; fi
}
# Observe-only: log, never act. Returns 0 if the reading is at/above the
# warn threshold (callers only use this to tag the summary line).
serial_bringup_temp_check() {
    local t="$1" unit="$2" s="$3"
    [[ "${t}" == "-" ]] && return 1
    if [[ ${t} -ge ${SERIAL_BRINGUP_TEMP_WARN_C} ]]; then
        echo "$(serial_bringup_ts)  WARNING: temp ${t}C >= ${SERIAL_BRINGUP_TEMP_WARN_C}C at +${s}s of ${unit} -- observe only, sequence continues (thermal-ingest-guard acts on its next fire)"
        return 0
    fi
    return 1
}
serial_bringup_ts() { date +%T; }
# awk does the float compare -- bash has no float arithmetic.
serial_bringup_ge() { awk "BEGIN{exit !($1 >= $2)}"; }
serial_bringup_gt() { awk "BEGIN{exit !($1 > $2)}"; }
# is-active prints the state AND exits non-zero for anything but active.
serial_bringup_active() { local st; st=$(systemctl --user is-active "$1.service" 2>/dev/null); echo "${st:-unknown}"; }

# serial_bringup_unit ACTION UNIT [MIN_WINDOW_S]
# Returns systemctl's exit status for the start/restart itself; the wait
# never fails the caller.
serial_bringup_unit() {
    local action="$1" unit="$2" win="${3:-${SERIAL_BRINGUP_MIN_WINDOW_S}}"
    local step="${SERIAL_BRINGUP_SAMPLE_S}" cap="${SERIAL_BRINGUP_MAX_WAIT_S}" max="${SERIAL_BRINGUP_LOAD_MAX}"
    local base l st peak peak_at s rc t tmax tempwarn

    if [[ "${action}" == "start" && "${SERIAL_BRINGUP_SKIP_ACTIVE}" == "1" ]] \
       && [[ "$(serial_bringup_active "${unit}")" == "active" ]]; then
        echo "$(serial_bringup_ts)  ${unit}.service already active -- skip (load $(serial_bringup_load) temp $(serial_bringup_temp)C)"
        return 0
    fi

    # Never start a unit on top of a spike we are still riding. Normally
    # instant: the previous unit's window already ended with load < max.
    s=0
    while serial_bringup_ge "$(serial_bringup_load)" "${max}"; do
        if [[ ${s} -ge ${cap} ]]; then
            echo "$(serial_bringup_ts)  WARNING: load $(serial_bringup_load) still >= ${max} after ${cap}s cap -- ${action}ing ${unit} anyway"
            break
        fi
        echo "$(serial_bringup_ts)  load $(serial_bringup_load) >= ${max}, holding before ${action} ${unit}"
        sleep "${step}"; s=$((s + step))
    done

    base=$(serial_bringup_load)
    echo "$(serial_bringup_ts)  baseline load ${base} temp $(serial_bringup_temp)C"
    echo "$(serial_bringup_ts)  ${action} ${unit}.service"
    systemctl --user "${action}" "${unit}.service"
    rc=$?

    # Sample through the window; keep sampling past it while load >= max,
    # up to the cap. Temp is read alongside -- observe only (see header).
    peak=0; peak_at=0; s=0; tmax="-"; tempwarn=0
    while :; do
        sleep "${step}"; s=$((s + step))
        l=$(serial_bringup_load); st=$(serial_bringup_active "${unit}"); t=$(serial_bringup_temp)
        printf '%s  +%3ds  load %-6s temp %sC  %s\n' "$(serial_bringup_ts)" "${s}" "${l}" "${t}" "${st}"
        if serial_bringup_gt "${l}" "${peak}"; then peak=${l}; peak_at=${s}; fi
        if [[ "${t}" != "-" ]] && { [[ "${tmax}" == "-" ]] || [[ ${t} -gt ${tmax} ]]; }; then tmax=${t}; fi
        if serial_bringup_temp_check "${t}" "${unit}" "${s}"; then tempwarn=1; fi
        if [[ ${s} -ge ${win} ]]; then
            if ! serial_bringup_ge "${l}" "${max}"; then
                break
            fi
            if [[ ${s} -ge ${cap} ]]; then
                echo "$(serial_bringup_ts)  WARNING: load ${l} still >= ${max} after ${cap}s cap -- proceeding"
                break
            fi
            echo "$(serial_bringup_ts)  load ${l} >= ${max}, holding before next unit"
        fi
    done
    st=$(serial_bringup_active "${unit}")
    echo "$(serial_bringup_ts)  >>> ${unit}: baseline ${base} -> peak ${peak} at +${peak_at}s -> end $(serial_bringup_load)  [${st}]  temp max ${tmax}C$( [[ ${tempwarn} -eq 1 ]] && echo '  TEMP-WARN (observed, not acted on)' )"
    return ${rc}
}

# serial_bringup_run ACTION unit[:window_s] ...
# Runs the list in the given order, one at a time. Exit status is non-zero
# if any start/restart itself failed (the sequence still runs to the end --
# one bad unit must not leave the rest of the stack down).
serial_bringup_run() {
    local action="$1"; shift
    local spec unit win rc=0
    echo "$(serial_bringup_ts)  serial-bringup: ${action} $# unit(s), load gate < ${SERIAL_BRINGUP_LOAD_MAX}, cap ${SERIAL_BRINGUP_MAX_WAIT_S}s"
    for spec in "$@"; do
        unit="${spec%%:*}"
        win="${SERIAL_BRINGUP_MIN_WINDOW_S}"
        [[ "${spec}" == *:* ]] && win="${spec#*:}"
        serial_bringup_unit "${action}" "${unit}" "${win}" || rc=1
    done
    echo "$(serial_bringup_ts)  serial-bringup: done (final load $(serial_bringup_load))"
    return ${rc}
}

# Executed directly (not sourced): plain CLI over serial_bringup_run.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -u
    if [[ $# -lt 2 ]] || [[ "$1" != "start" && "$1" != "restart" ]]; then
        echo "usage: $0 <start|restart> unit[:window_s] ..." >&2
        exit 2
    fi
    serial_bringup_run "$@"
fi
