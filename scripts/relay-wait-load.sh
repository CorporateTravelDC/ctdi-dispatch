#!/usr/bin/env bash
# scripts/relay-wait-load.sh -- wait until the box has load headroom before a
# relay starts heavy work (tests, rollouts, backfills, transient jobs).
#
# Operator 2026-10-18: "make the relays load aware". On 2026-10-17 relay work
# stacked on a 20-30 baseline and tripped the thermal-ingest-guard LOCKDOWN
# (load1 >= 40, whole stack shed) twice. Relays now wait instead: every heavy
# step is preceded by this script (relay-lint enforces it).
#
#   RELAY_MAX_LOAD          wait while load1 >= this (default 24; lockdown is 40)
#   RELAY_LOAD_POLL_S       seconds between checks (default 15)
#   RELAY_LOAD_WAIT_MAX_S   give up after this long, exit 1 (default 3600); a
#                           rerun of the relay picks up where it stopped
#   LOADAVG_FILE            test hook (default /proc/loadavg)
set -uo pipefail
MAX="${RELAY_MAX_LOAD:-24}"
POLL="${RELAY_LOAD_POLL_S:-15}"
LIMIT="${RELAY_LOAD_WAIT_MAX_S:-3600}"
SRC="${LOADAVG_FILE:-/proc/loadavg}"
waited=0
said=0
while :; do
    l="$(cut -d' ' -f1 "$SRC")"
    if awk -v l="$l" -v m="$MAX" 'BEGIN { exit !(l < m) }'; then
        [[ $said == 1 ]] && echo "relay-wait-load: load1 $l, continuing after ${waited}s"
        exit 0
    fi
    if [[ $said == 0 ]]; then
        echo "relay-wait-load: load1 $l >= $MAX -- waiting (lockdown line is 40)"
        said=1
    fi
    if (( waited >= LIMIT )); then
        echo "relay-wait-load: load1 still >= $MAX after ${LIMIT}s -- stop; rerun the relay later" >&2
        exit 1
    fi
    sleep "$POLL"
    waited=$((waited + POLL))
done
