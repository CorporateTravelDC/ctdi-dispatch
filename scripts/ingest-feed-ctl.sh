#!/bin/bash
# scripts/ingest-feed-ctl.sh
# Control surface for the seven ingest containers created by the 2026-07-26
# per-feed split (corporatetraveldc-ingest-{core,fdps,stdds,tfms,tbfm,itws,notam}).
# Each is a real, independent systemd --user unit/container now -- stopping
# one actually frees its CPU/memory, unlike the old in-process bandwidth-
# priority soft-pause (which stays connected and just stops draining the
# queue -- that mechanism still exists separately, see
# /admin/bandwidth-priority, and is unaffected by this script).
#
# Usage:
#   ingest-feed-ctl.sh status
#   ingest-feed-ctl.sh stop    <target>
#   ingest-feed-ctl.sh start   <target> [--stagger=Ns] [--order=lightest-first|heaviest-first]
#   ingest-feed-ctl.sh restart <target> [--stagger=Ns] [--order=lightest-first|heaviest-first]
#
# <target> is one of:
#   all                    -- all seven (core always first/last per direction below)
#   core                   -- NWWS-OI + Amtrak + local airspace, no SWIM feeds
#   fdps|stdds|tfms|tbfm|itws|notam   -- a single SWIM feed
#   a comma-separated list of the above, e.g. "tbfm,itws,notam"
#
# --stagger=Ns   seconds to wait between each unit in the sequence (default 15).
#                Only matters for start/restart with more than one target;
#                stop always fires everything at once (no reason to delay
#                freeing resources).
# --order        lightest-first (default) or heaviest-first. Determines the
#                order the six SWIM feeds come up in when target=all or
#                target lists more than one feed. "core" is never reordered
#                by this flag -- it always goes first, since NWWS-OI/Amtrak/
#                local_airspace have no SWIM contention profile and nothing
#                downstream should wait on them.
#
# Weight ordering (heaviest to lightest) is from real observed volume the
# night this was built (2026-07-26): fdps and stdds run continuously and
# are the dominant CPU/bandwidth consumers; tfms and tbfm are moderate;
# itws and notam are lowest-volume (notam in particular reports "wrote 0"
# most cycles -- see docs/DATA_SOURCES.md).
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

ALL_FEEDS=(fdps stdds tfms tbfm itws notam)
# heaviest -> lightest
HEAVY_ORDER=(fdps stdds tfms tbfm itws notam)
# lightest -> heaviest (reverse of the above)
LIGHT_ORDER=(notam itws tbfm tfms stdds fdps)

UNIT_PREFIX="corporatetraveldc-ingest-"

usage() {
    sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
}

unit_name() { echo "${UNIT_PREFIX}${1}"; }

is_valid_target() {
    [[ "$1" == "core" ]] && return 0
    for f in "${ALL_FEEDS[@]}"; do
        [[ "$1" == "${f}" ]] && return 0
    done
    return 1
}

# Expand a target spec ("all" | "core" | "fdps" | "a,b,c") into an ordered
# list of unit short-names, applying the weight order for multi-feed cases.
expand_targets() {
    local spec="$1" order="$2"
    local -a order_list=()
    if [[ "${order}" == "heaviest-first" ]]; then
        order_list=("${HEAVY_ORDER[@]}")
    else
        order_list=("${LIGHT_ORDER[@]}")
    fi

    if [[ "${spec}" == "all" ]]; then
        echo "core"
        printf '%s\n' "${order_list[@]}"
        return 0
    fi

    # comma-separated explicit list -- preserve requested order verbatim,
    # just validate each entry.
    IFS=',' read -ra parts <<< "${spec}"
    for p in "${parts[@]}"; do
        p="$(echo "${p}" | tr -d '[:space:]')"
        if ! is_valid_target "${p}"; then
            echo "unknown target: '${p}' (valid: all, core, ${ALL_FEEDS[*]// /,})" >&2
            exit 1
        fi
        echo "${p}"
    done
}

do_status() {
    printf '%-8s %-10s %-10s\n' "UNIT" "ACTIVE" "SUB"
    for name in core "${ALL_FEEDS[@]}"; do
        u="$(unit_name "${name}")"
        active="$(systemctl --user is-active "${u}.service" 2>/dev/null || true)"
        sub="$(systemctl --user show "${u}.service" -p SubState --value 2>/dev/null || true)"
        printf '%-8s %-10s %-10s\n' "${name}" "${active:-unknown}" "${sub:-unknown}"
    done
}

do_stop() {
    local spec="$1"
    local -a targets=()
    mapfile -t targets < <(expand_targets "${spec}" "lightest-first")
    echo "stopping: ${targets[*]} (all at once -- no reason to stagger a stop)"
    local -a units=()
    for t in "${targets[@]}"; do units+=("$(unit_name "${t}").service"); done
    systemctl --user stop "${units[@]}"
}

do_start_or_restart() {
    local action="$1" spec="$2" stagger="$3" order="$4"
    local -a targets=()
    mapfile -t targets < <(expand_targets "${spec}" "${order}")
    echo "${action}ing (order=${order}, stagger=${stagger}s): ${targets[*]}"
    local first=1
    for t in "${targets[@]}"; do
        if [[ ${first} -eq 0 ]]; then
            sleep "${stagger}"
        fi
        first=0
        u="$(unit_name "${t}")"
        echo "  -> ${action} ${u}"
        systemctl --user "${action}" "${u}.service"
    done
}

[[ $# -ge 1 ]] || usage
cmd="$1"; shift || true

STAGGER=15
ORDER="lightest-first"
TARGET=""
for arg in "$@"; do
    case "${arg}" in
        --stagger=*) STAGGER="${arg#--stagger=}" ;;
        --order=*)   ORDER="${arg#--order=}" ;;
        *)           TARGET="${arg}" ;;
    esac
done

case "${cmd}" in
    status)
        do_status
        ;;
    stop)
        [[ -n "${TARGET}" ]] || usage
        do_stop "${TARGET}"
        ;;
    start)
        [[ -n "${TARGET}" ]] || usage
        do_start_or_restart "start" "${TARGET}" "${STAGGER}" "${ORDER}"
        ;;
    restart)
        [[ -n "${TARGET}" ]] || usage
        do_start_or_restart "restart" "${TARGET}" "${STAGGER}" "${ORDER}"
        ;;
    *)
        usage
        ;;
esac
