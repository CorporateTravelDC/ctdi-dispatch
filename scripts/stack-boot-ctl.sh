#!/bin/bash
# scripts/stack-boot-ctl.sh
# Staggered startup control for the 19 non-ingest long-running Quadlet
# containers (everything except the seven SWIM ingest units, which have
# their own staggered control surface -- see scripts/ingest-feed-ctl.sh
# and corporatetraveldc-boot-stagger.service). Verified directly against
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
#   stack-boot-ctl.sh start  [--stagger=Ns]   (default 15s)
#   stack-boot-ctl.sh status
#
# Order below is dependency-aware, not just weight-aware like the SWIM
# side -- this fleet is heterogeneous (web app, DB, media/ACARS decoders,
# demo stack, mail bridge), so the sequence follows each unit's real
# After= chain: infra core first (ntfy, poller, web), then everything
# that depends on poller/web, then independent heavier services
# (ultrafeeder, acarsrouter, protonbridge, nextcloud-db, csexec-contact),
# then their dependents (acarshub, dumpvdl2, nextcloud-app), then the
# demo/runner chain last (lowest operational priority).
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

UNIT_PREFIX="corporatetraveldc-"

# name -> actual systemd --user unit name (a few don't carry the prefix)
declare -A UNIT_NAME=(
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
)

# Dependency-aware boot order -- see header comment for the reasoning
# behind each layer.
ORDER=(
  ntfy
  poller
  web
  pusher
  ultrafeeder
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
  runner
  demo-api
  runner-demo
)

STAGGER=15

usage() {
    sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
}

cmd_start() {
    local total=${#ORDER[@]} i=0
    echo "stack-boot-ctl: starting ${total} units, ${STAGGER}s apart"
    for name in "${ORDER[@]}"; do
        i=$((i + 1))
        unit="${UNIT_NAME[$name]}.service"
        echo "[$i/$total] starting ${unit}"
        systemctl --user start "${unit}"
        if [[ $i -lt $total ]]; then
            sleep "${STAGGER}"
        fi
    done
    echo "stack-boot-ctl: all ${total} units started"
}

cmd_status() {
    for name in "${ORDER[@]}"; do
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
