#!/bin/bash
# scripts/stack-boot-ctl.sh
# Serialized, load-gated startup control for every long-running Quadlet
# containers: originally the 19 non-ingest units only (the seven SWIM
# ingest units had their own staggered control surface -- see
# scripts/ingest-feed-ctl.sh and corporatetraveldc-boot-stagger.service);
# since 2026-09-06 the ingest units lead this sequence too. Verified against
# each .container file's [Install] section, not a podman-ps snapshot count
# -- a naive "24 running minus 7 ingest" guess undercounts by one, since
# one of the 24 was a timer-triggered oneshot skill container caught
# mid-run, not a WantedBy=default.target long-running unit.
#
# Built 2026-07-28. Until this existed, these 18 units all carried their
# own WantedBy=default.target and fired in parallel at every boot --
# exactly the pattern that caused the boot-storm crashes the ingest side
# was already fixed for (see corporatetraveldc-ingest-itws.container's
# [Install] comment). A cold reboot could bring poller, web, pusher,
# ultrafeeder, the full ACARS stack, both Nextcloud units, openwebui,
# protonbridge, and the demo/runner stack up simultaneously the instant
# network-online.target was reached. Fixed the same way the ingest side
# was: no WantedBy on the individual units, one oneshot orchestrator
# (corporatetraveldc-stack-boot-stagger.service) owns startup order.
#
# Usage:
#   stack-boot-ctl.sh start  [--stagger=Ns]   (default 60s, see below)
#   stack-boot-ctl.sh status
#
# 2026-09-06 (operator directive, work-order item 1): the fixed-delay
# stagger is replaced by the serialized, load-gated bring-up from
# scripts/lib/serial-bringup.sh -- one unit at a time, load1 sampled every
# 10 s, the next unit not started until the unit's minimum window has
# elapsed AND load1 < SERIAL_BRINGUP_LOAD_MAX (12), hard-capped at
# SERIAL_BRINGUP_MAX_WAIT_S (300) per unit. Proven live the same day by
# scripts/serialized-rollout.sh: whole stack up at peak load 9.61 versus
# 40.28 (LOCKDOWN) for a flat restart. --stagger=Ns now sets the default
# per-unit MINIMUM window (it is no longer the whole delay); per-unit
# windows in ORDER (name:seconds) override it, copied from the rollout.
# The seven SWIM ingest units moved in here at the head of ORDER so ONE
# sequence owns the entire boot; corporatetraveldc-boot-stagger.service
# still runs afterwards as a safety net and finds them already active
# (library skips an active unit on "start" without a window).
#
# Order below is dependency-aware, not just weight-aware like the SWIM
# side -- this fleet is heterogeneous (web app, DB, media/ACARS decoders,
# demo stack, mail bridge), so the sequence follows each unit's real
# After= chain: SWIM feeds lightest-first then ingest-core (the same
# LIGHT_ORDER as ingest-feed-ctl.sh), infra core (poller, pusher, web,
# runner, ntfy), then independent heavier services (ultrafeeder,
# acarsrouter, protonbridge, nextcloud-db, csexec-contact), then their
# dependents (acarshub, dumpvdl2, nextcloud-app), then the demo/runner
# chain last (lowest operational priority).
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/serial-bringup.sh
. "${SCRIPT_DIR}/lib/serial-bringup.sh"

UNIT_PREFIX="corporatetraveldc-"

# name -> actual systemd --user unit name (a few don't carry the prefix)
declare -A UNIT_NAME=(
  [ingest-notam]="${UNIT_PREFIX}ingest-notam"
  [ingest-itws]="${UNIT_PREFIX}ingest-itws"
  [ingest-tbfm]="${UNIT_PREFIX}ingest-tbfm"
  [ingest-tfms]="${UNIT_PREFIX}ingest-tfms"
  [ingest-stdds]="${UNIT_PREFIX}ingest-stdds"
  [ingest-fdps]="${UNIT_PREFIX}ingest-fdps"
  [ingest-core]="${UNIT_PREFIX}ingest-core"
  [pgsql]="${UNIT_PREFIX}pgsql"
  [ntfy]="ntfy"
  [poller]="${UNIT_PREFIX}poller"
  [web]="${UNIT_PREFIX}web"
  [ultrafeeder]="${UNIT_PREFIX}ultrafeeder"
  [acarsrouter]="${UNIT_PREFIX}acarsrouter"
  [protonbridge]="${UNIT_PREFIX}protonbridge"
  [nextcloud-db]="nextcloud-db"
  [csexec-contact]="csexec-contact"
  [pusher]="${UNIT_PREFIX}pusher"
  [acarshub]="${UNIT_PREFIX}acarshub"
  [dumpvdl2]="${UNIT_PREFIX}dumpvdl2"
  [nextcloud-app]="nextcloud-app"
  [openwebui]="openwebui"
  [rss-bridge]="rss-bridge"
  [demo]="${UNIT_PREFIX}demo"
  [acars-watcher]="${UNIT_PREFIX}acars-watcher"
  [runner]="${UNIT_PREFIX}runner"
  [demo-api]="${UNIT_PREFIX}demo-api"
  [runner-demo]="${UNIT_PREFIX}runner-demo"
  [amtrak-tracker]="amtrak-tracker"
  [piaware]="${UNIT_PREFIX}piaware"
  [fr24feed]="${UNIT_PREFIX}fr24feed"
  [planefinder]="${UNIT_PREFIX}planefinder"
  [airnavradar]="${UNIT_PREFIX}airnavradar"
)

# Dependency-aware boot order -- see header comment for the reasoning
# behind each layer. "name:seconds" = that unit's minimum window (values
# from serialized-rollout.sh's live run); bare names use --stagger/the
# SERIAL_BRINGUP_MIN_WINDOW_S default. ntfy stays first: every ingest
# unit carries Wants=/After=ntfy.service, so a feed started before it
# would pull ntfy in implicitly, outside this sequence's pacing.
# 2026-09-06 (later the same day): pgsql, amtrak-tracker and the four
# ADS-B feeders joined this list when their WantedBy=default.target was
# removed -- until then those six, plus the seven ingest units, were
# pulled up flat by default.target in the first seconds of the session,
# a full minute before this sequence began (Sep 5 boot journal), so the
# stagger never owned the boot. pgsql goes before poller (its future
# primary consumer, docs/POSTGRES_MIGRATION.md); the feeders follow
# ultrafeeder, which each of them Wants=/After=.
ORDER=(
  ntfy:30
  pgsql:30
  ingest-notam
  ingest-itws
  ingest-tbfm
  ingest-tfms
  ingest-stdds
  ingest-fdps:90
  ingest-core:90
  poller:180
  pusher
  web:90
  runner:90
  # ultrafeeder/acarsrouter/acarshub/dumpvdl2 re-added 2026-08-19 -- both
  # SDR dongles confirmed enumerating again via lsusb (RTL2838 x2, bus 001
  # + bus 003) after the operator's hardware replacement; ultrafeeder,
  # acarsrouter, and dumpvdl2 had already been manually restarted and were
  # running fine, but acarshub was missed and stayed down across a reboot
  # (excluded here, no WantedBy of its own), producing a standing 502 on
  # acars.example.com until restarted by hand. If a future SDR
  # casualty recurs, pull these four back out rather than leaving them in
  # and fighting the watchdogs blind -- see git history on this block for
  # the prior exclusion's reasoning.
  ultrafeeder
  piaware
  fr24feed
  planefinder
  airnavradar
  acarsrouter
  protonbridge
  nextcloud-db
  csexec-contact
  acarshub
  dumpvdl2
  nextcloud-app
  openwebui
  rss-bridge
  demo
  acars-watcher
  demo-api
  runner-demo
  amtrak-tracker
)

# --stagger=Ns: default per-unit minimum window (was the fixed delay).
STAGGER="${SERIAL_BRINGUP_MIN_WINDOW_S}"

usage() {
    sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
}

cmd_start() {
    local total=${#ORDER[@]} i=0 name win unit
    SERIAL_BRINGUP_MIN_WINDOW_S="${STAGGER}"
    echo "stack-boot-ctl: starting ${total} units serially, min window ${STAGGER}s, load gate < ${SERIAL_BRINGUP_LOAD_MAX}, cap ${SERIAL_BRINGUP_MAX_WAIT_S}s"
    for spec in "${ORDER[@]}"; do
        i=$((i + 1))
        name="${spec%%:*}"; win="${STAGGER}"
        [[ "${spec}" == *:* ]] && win="${spec#*:}"
        unit="${UNIT_NAME[$name]}"
        echo "[$i/$total] starting ${unit}.service (min window ${win}s)"
        serial_bringup_unit start "${unit}" "${win}"
    done
    echo "stack-boot-ctl: all ${total} units started"
}

cmd_status() {
    for spec in "${ORDER[@]}"; do
        name="${spec%%:*}"
        unit="${UNIT_NAME[$name]}.service"
        state=$(systemctl --user is-active "${unit}" 2>/dev/null || echo "unknown")
        printf '%-20s %-20s %s\n' "${name}" "${unit}" "${state}"
    done
}

[[ $# -lt 1 ]] && usage
action="$1"
shift

for arg in "$@"; do
    case "$arg" in
        --stagger=*) STAGGER="${arg#--stagger=}" ;;
        *) echo "unknown argument: $arg" >&2; usage ;;
    esac
done

case "$action" in
    start)  cmd_start ;;
    status) cmd_status ;;
    *) usage ;;
esac
