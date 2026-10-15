#!/bin/bash
# scripts/healthcheck-ping.sh -- external dead-man switch (healthchecks.io), 2026-10-15.
#
# Why: on 2026-10-15 the box was offline 11:27Z-13:46Z (the failover watcher had
# routed it into the dead backup hotspot) and nothing could say so: ntfy, the
# watchdog and every alert path live on this same box. healthchecks.io alerts
# from OUTSIDE when these pings stop.
#
# Every run: if the local dispatch /healthz answers status=ok, ping the check;
# otherwise ping <url>/fail with a one-line reason, so "box up but stack down"
# alerts as well as "box dark". The body carries only load1 and the health word.
#
# The ping URL is a credential-like secret (anyone holding it can fake "alive"):
# it lives in ~/.secrets/healthchecks_ping.url (0600, never tracked) and reaches
# curl through a config on stdin, never argv (/proc/<pid>/cmdline is readable).
#   healthcheck-ping.sh            normal run (user timer, every 5 min)
#   healthcheck-ping.sh --dry-run  print what would be sent, send nothing
set -uo pipefail
URL_FILE="${HC_PING_URL_FILE:-$HOME/.secrets/healthchecks_ping.url}"
HEALTHZ="${HC_HEALTHZ_URL:-http://127.0.0.1:8000/healthz}"
DRY=0; [[ "${1:-}" == "--dry-run" ]] && DRY=1

if [[ ! -s "${URL_FILE}" ]]; then
    echo "healthcheck-ping: no ping URL in ${URL_FILE} -- nothing sent" >&2
    exit 0      # not configured is not a failure of this unit
fi
url="$(tr -d '[:space:]' < "${URL_FILE}")"
if [[ ! "${url}" =~ ^https://hc-ping\.com/[A-Za-z0-9/_-]+$ ]]; then
    echo "healthcheck-ping: ${URL_FILE} does not hold an https://hc-ping.com/... URL -- nothing sent" >&2
    exit 1
fi

load1="$(cut -d' ' -f1 /proc/loadavg)"
status="$(curl -s --max-time 10 "${HEALTHZ}" 2>/dev/null | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("status") or "unknown")
except Exception: print("unreachable")' 2>/dev/null)"
status="${status:-unreachable}"

target="${url}"; [[ "${status}" == "ok" ]] || target="${url}/fail"
body="healthz=${status} load1=${load1}"
if (( DRY )); then
    echo "dry-run: would send '${body}' to the $( [[ ${status} == ok ]] && echo success || echo fail ) endpoint"
    exit 0
fi
# URL via a curl config on stdin, never on the command line
if printf 'url = "%s"\n' "${target}" | curl -fsS --max-time 15 --retry 3 --retry-delay 5 \
        -K - --data-raw "${body}" -o /dev/null 2>/dev/null; then
    echo "healthcheck-ping: sent (${body})"
else
    echo "healthcheck-ping: send failed (${body}) -- healthchecks.io will alert on the missed ping" >&2
fi
exit 0
