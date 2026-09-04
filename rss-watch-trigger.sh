#!/usr/bin/env bash
# scripts/rss-watch-trigger.sh
#
# Manual on-demand trigger for the per-category RSS watch skills (AAM,
# aviation, any future ones added to CATEGORY_SERVICES below). Operator
# request 2026-08-06: "make each category's scrape independently
# manually-triggerable (both 'run all categories now' and 'run just this
# one category now')."
#
# This is a thin wrapper around `systemctl --user start`, nothing more --
# a manual run executes the EXACT SAME skill code, writing to the EXACT
# SAME cache files and vault paths a scheduled run would. There is no
# separate "manual mode" data path, which is what makes manually-surfaced
# content automatically visible to the next scheduled brief AND to
# second_brain_weekly.py's rollup (see that skill's 2026-08-06 docstring
# note on scanning 04-Syntheses/daily/) -- nothing extra to keep in sync.
#
# `systemctl --user start` on a Type=oneshot service blocks until it
# finishes (these calls take several minutes each, real Ollama synthesis
# time) -- that's expected, not a bug in this script. Running "all"
# triggers each category SEQUENTIALLY on purpose: they share the same
# Ollama model/slot (see common/ollama_lock.py), so running them
# "in parallel" would just queue the second one behind the first anyway,
# sequential is simpler and makes the wait time predictable.
#
# Usage:
#   scripts/rss-watch-trigger.sh aam        # just the AAM daily watch
#   scripts/rss-watch-trigger.sh aviation   # just the aviation daily watch
#   scripts/rss-watch-trigger.sh all        # every category, in sequence
#   scripts/rss-watch-trigger.sh --list     # show available categories

set -uo pipefail

# category name -> systemd --user service unit (without .service suffix)
declare -A CATEGORY_SERVICES=(
    [aam]="corporatetraveldc-aam-daily-watch"
    [aviation]="corporatetraveldc-aviation-daily-watch"
    [gig-economy]="corporatetraveldc-gig-economy-daily-watch"
    [concierge-travel]="corporatetraveldc-concierge-travel-daily-watch"
    [trains-yachts]="corporatetraveldc-trains-yachts-daily-watch"
    [executive-protection]="corporatetraveldc-executive-protection-daily-watch"
)

usage() {
    local exit_code="${1:-1}"
    echo "Usage: $0 <category|all|--list>"
    echo "Available categories:"
    for cat in "${!CATEGORY_SERVICES[@]}"; do
        echo "  $cat -> ${CATEGORY_SERVICES[$cat]}.service"
    done
    exit "$exit_code"
}

run_one() {
    local cat="$1"
    local svc="${CATEGORY_SERVICES[$cat]}"
    echo "[rss-watch-trigger] starting $cat ($svc.service) -- this blocks until it finishes (real Ollama call, several minutes)..."
    if systemctl --user start "${svc}.service"; then
        echo "[rss-watch-trigger] $cat: OK"
        return 0
    else
        echo "[rss-watch-trigger] $cat: FAILED -- check: systemctl --user status ${svc}.service"
        return 1
    fi
}

[[ $# -eq 1 ]] || usage

case "$1" in
    --list|-l)
        usage 0
        ;;
    all)
        overall_rc=0
        for cat in "${!CATEGORY_SERVICES[@]}"; do
            run_one "$cat" || overall_rc=1
        done
        exit "$overall_rc"
        ;;
    *)
        if [[ -z "${CATEGORY_SERVICES[$1]+x}" ]]; then
            echo "[rss-watch-trigger] unknown category: $1"
            usage
        fi
        run_one "$1"
        exit $?
        ;;
esac
