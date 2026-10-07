#!/usr/bin/env bash
# scripts/maintenance-dispatch.sh -- window-aware dispatcher for the long-runner
# queue (operator directive 2026-10-04: rolling, operationally-aware
# maintenance windows -- 3-4 candidates through the day from the 30-day quiet
# profile -- instead of one fixed 23:00-05:00 block).
#
# A job with a fixed OnCalendar cannot "wait for a quiet window", so the jobs
# that should ride the rolling windows are ENROLLED here (scripts/lib/
# maintenance-queue.txt, one unit per line, priority order) and their timers
# retired. This dispatcher runs every ~20 min and, when
# `maintenance-window-guard.sh --rolling` says we are inside today's window:
#   * starts the first enrolled unit not yet run today (operational day rolls
#     at 05:00 ET), ONE at a time -- it never starts a job while another
#     enrolled job is active (they also share long-runner-unit.lock);
#   * records dispatch/outcome in ~/.cache/maintenance-queue.json (scripts/lib/
#     maintenance_queue.py: dispatched -> done | failed | skipped, one retry).
# Outside a window it exits 0 silently. If the operational day ends with
# enrolled jobs unrun (not enough window time), it logs + ntfy's the list so
# a skip is never silent -- and the quiet-window profile has one more data
# point for why.
#
#   --status   print today's queue state (pending/dispatched/done/failed/skipped)
#   --now      ignore the window (operator-run); still one at a time
set -uo pipefail
# 2026-10-05 (argv-token sweep): a bearer token never goes on a command line --
# /proc/<pid>/cmdline is world-readable here (no hidepid), i.e. readable by
# every team account. authhdr NAME TOKEN puts the header on a private fd and
# sets NAME=(-H @/dev/fd/N) for ONE curl call (re-run it before each call).
authhdr() { local -n _ah="$1"; [[ -n "${_AUTHHDR_FD:-}" ]] && exec {_AUTHHDR_FD}<&-; exec {_AUTHHDR_FD}<<<"Authorization: Bearer $2"; _ah=(-H "@/dev/fd/${_AUTHHDR_FD}"); }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUEUE_FILE="${REPO_ROOT}/scripts/lib/maintenance-queue.txt"
STATE="${HOME}/.cache/maintenance-queue.json"; mkdir -p "${HOME}/.cache"
GUARD="${REPO_ROOT}/scripts/maintenance-window-guard.sh"
ENV_FILE=/etc/corporatetraveldc/dispatch.env; SECRETS_FILE=/etc/corporatetraveldc/dispatch-secrets.env
log() { printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "$2"; }
read_env_var() { local key="$1" file="$2"; [[ -f "$file" ]] || return 0; grep -m1 "^${key}=" "$file" 2>/dev/null | cut -d'=' -f2-; }
NTFY_BASE="$(read_env_var NTFY_BASE_URL "$ENV_FILE")"; NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_OPS="$(read_env_var NTFY_OPS_TOPIC "$ENV_FILE")";  NTFY_OPS="${NTFY_OPS:-ops-health}"
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "$SECRETS_FILE")"
ntfy_send() { local auth=(); [[ -n "$NTFY_TOKEN" ]] && authhdr auth "${NTFY_TOKEN}"
  curl -sf --max-time 5 "${auth[@]}" -H "Title: $1" -H "Priority: ${3:-2}" -H "Tags: hourglass" -d "$2" "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1 || true; }

# operational day: 05:00 ET rollover (matches second_brain_daily's rule)
opday() { TZ=America/New_York date -d '5 hours ago' +%F; }
TODAY=$(opday)

mapfile -t QUEUE < <(grep -vE '^\s*(#|$)' "$QUEUE_FILE" 2>/dev/null)
(( ${#QUEUE[@]} )) || { log warn "queue file empty: ${QUEUE_FILE}"; exit 0; }

# state machine lives in scripts/lib/maintenance_queue.py (testable); this
# script only gathers live facts (is-active, journal lines) and acts.
MQ=(python3 "${REPO_ROOT}/scripts/lib/maintenance_queue.py" "$STATE" "$TODAY")

NOW=0; STATUS=0
for a in "$@"; do case "$a" in --now) NOW=1 ;; --status) STATUS=1 ;; esac; done

is_active() { local st; st=$(systemctl --user is-active "$1.service" 2>/dev/null); [[ "$st" == active || "$st" == activating ]]; }

# 1. reconcile every dispatched job from the journal since its dispatch
JTMP=$(mktemp); trap 'rm -f "$JTMP"' EXIT
for u in "${QUEUE[@]}"; do
  at=$("${MQ[@]}" dispatched-at "$u"); [[ "$at" =~ ^[0-9]+$ ]] && (( at > 0 )) || continue
  a=0; is_active "$u" && a=1
  journalctl --user -u "$u.service" --since "@$at" --no-pager -o cat >"$JTMP" 2>/dev/null || : >"$JTMP"
  out=$("${MQ[@]}" reconcile "$u" "$a" "$JTMP")
  case "$out" in failed|failed-final) log warn "${u}: ${out}" ;; skipped|skipped-final) log info "${u}: ${out}" ;; esac
  [[ "$out" == failed-final ]] && ntfy_send "maintenance-dispatch: ${u} failed twice" "${TODAY}: ${u} failed on both attempts today; queue continued without it" 3
done

if (( STATUS )); then
  echo "operational day: ${TODAY}"; "$GUARD" --rolling --check | grep -E 'rolling|result'
  "${MQ[@]}" status "$QUEUE_FILE" | while read -r u st n; do
    is_active "$u" && st="RUNNING"
    printf '  %-52s %-14s attempts=%s\n' "$u" "$st" "$n"
  done; exit 0
fi

# 2. one at a time: never start while an enrolled job is active
for u in "${QUEUE[@]}"; do is_active "$u" && exit 0; done

# 3. next job: never-tried first, then one retry for failed/skipped
NEXT=$("${MQ[@]}" next "$QUEUE_FILE")
[[ -n "$NEXT" ]] || exit 0

if (( ! NOW )) && ! "$GUARD" --rolling >/dev/null 2>&1; then
  # outside a window; in the operational day's last hour (04:00-05:00 ET) say
  # once which enrolled jobs never completed today.
  hour=$(TZ=America/New_York date +%H)
  if [[ "$hour" == 04 ]] && [[ "$("${MQ[@]}" get-flag unrun_notified)" == 0 ]]; then
    pend=$("${MQ[@]}" unrun "$QUEUE_FILE")
    log warn "operational day ${TODAY} ending with unrun enrolled jobs: ${pend}"
    ntfy_send "maintenance-dispatch: unrun today" "${TODAY}: ${pend} -- not enough rolling-window time or failed; see --status" 3
    "${MQ[@]}" flag unrun_notified
  fi
  exit 0
fi

# 4. dispatch without blocking; outcome is reconciled on a later pass.
# Position is computed AFTER the state update (duel X3).
if systemctl --user start --no-block "$NEXT.service"; then
  pos=$("${MQ[@]}" dispatched "$NEXT" "$QUEUE_FILE")
  log info "inside rolling window -- dispatched ${NEXT}.service (queue position ${pos})"
else
  log error "systemctl refused to queue ${NEXT}.service"; exit 1
fi
exit 0
