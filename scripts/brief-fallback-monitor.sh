#!/usr/bin/env bash
# brief-fallback-monitor.sh — Guard 3 of the brief-generation regression guard
# (2026-08-08). Alerts LOUDLY (ntfy max priority) when ops-brief / ep-advance
# briefs revert to the DETERMINISTIC fallback for N consecutive runs (or >=half a
# rolling window). This is the exact silent-degradation failure mode that went
# undiagnosed as thermal/swap for a long time — root cause was gemma3's Sliding
# Window Attention breaking the Ollama KV cache so every brief blew the 240s
# timeout. Read-only: it detects + alerts, never restarts anything.
#
# Log strings it classifies (from ops_brief.py / ep_advance.py):
#   success  -> "...: brief generated (Ollama/<model>)"
#   fallback -> "...: brief generated (deterministic[ fallback])"
#
# 2026-08-18: the alert text below used to hardcode "gemma3-SWA" as the
# assumed root cause -- accurate for the original 2026-08-08 incident,
# WRONG since the 2026-08-15 Phase 4 rebuild moved every brief model to
# phi3:mini (gemma3 is fully evicted, see build-models.sh GUARD 1). Left
# stale for 3 days, actively misdirecting whoever reads the alert. Text
# is now failure-class-agnostic -- check the actual journal
# (`journalctl --user -u <unit>` around the fallback timestamp, grep for
# "llm:" WARNING/INFO lines from common/llm.py's sanitize_llm_response())
# for what's actually happening; as of this fix the live cause is
# repetition-loop hallucinations getting correctly caught and discarded
# by that guard, not a timeout or connectivity failure.
set -uo pipefail
# 2026-10-05 (argv-token sweep): a bearer token never goes on a command line --
# /proc/<pid>/cmdline is world-readable here (no hidepid), i.e. readable by
# every team account. authhdr NAME TOKEN puts the header on a private fd and
# sets NAME=(-H @/dev/fd/N) for ONE curl call (re-run it before each call).
authhdr() { local -n _ah="$1"; [[ -n "${_AUTHHDR_FD:-}" ]] && exec {_AUTHHDR_FD}<&-; exec {_AUTHHDR_FD}<<<"Authorization: Bearer $2"; _ah=(-H "@/dev/fd/${_AUTHHDR_FD}"); }

ENV_FILE=/etc/corporatetraveldc/dispatch.env
SECRETS_FILE=/etc/corporatetraveldc/dispatch-secrets.env
WINDOW="${BRIEF_FALLBACK_WINDOW:-6}"               # rolling window size (runs)
UNITS=(corporatetraveldc-ops-brief corporatetraveldc-ep-advance)

read_env_var() {  # KEY=VALUE from an env file, quotes stripped
  local key="$1" file="$2"
  [ -r "$file" ] || return 0
  sed -nE "s/^${key}=(.*)$/\1/p" "$file" | tail -1 | sed -E "s/^\"(.*)\"$/\1/; s/^'(.*)'$/\1/"
}

NTFY_BASE="$(read_env_var NTFY_BASE_URL "$ENV_FILE")"; NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "$SECRETS_FILE")"; NTFY_TOKEN="${NTFY_TOKEN%%:*}"
ALERT_TOPIC="${BRIEF_FALLBACK_TOPIC:-ops-health}"

ntfy_alert() {  # $1=title $2=body ; MAX priority + token auth (no token == silent 403)
  if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "  [DRY_RUN] would ntfy MAX -> ${NTFY_BASE}/${ALERT_TOPIC} :: $1"
    return 0
  fi
  local auth=()
  [ -n "$NTFY_TOKEN" ] && authhdr auth "${NTFY_TOKEN}"
  curl -s -m 10 "${auth[@]}" \
    -H "Title: $1" -H "Priority: max" -H "Tags: rotating_light,robot" \
    -d "$2" "${NTFY_BASE}/${ALERT_TOPIC}" >/dev/null 2>&1 || true
}

# Lean-out L2 / F4 (2026-10-19): this used to re-read 2 days of each unit's
# journal every hour (`--since "2 days ago" -n 1000`, peaks of ~1.2 GB mapped).
# Now each run reads only the entries after a per-unit cursor
# (scripts/lib/journal-cursor.sh) and appends the classified outcomes to a
# small per-unit history; the window is the last $WINDOW outcomes of that
# history, exactly as before. First run (or a cursor older than 1 day): the old
# 2-day read, once.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/journal-cursor.sh
. "${REPO_ROOT}/scripts/lib/journal-cursor.sh"
STATE_DIR="${BRIEF_FALLBACK_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/corporatetraveldc/brief-fallback-monitor}"
mkdir -p "$STATE_DIR"
HISTORY_KEEP=50

# One outcome per run (dedupe the journal's double-logging by timestamp), newest last.
classify_lines() {  # stdin: journal lines (short format) -> "Mon DD HH:MM:SS OUTCOME"
  grep -aE 'brief generated' \
    | sed -E "s/^([A-Z][a-z]{2}[[:space:]]+[0-9]{1,2}[[:space:]]+[0-9:]{8}).*[Dd]eterministic.*/\1 FALLBACK/; s/^([A-Z][a-z]{2}[[:space:]]+[0-9]{1,2}[[:space:]]+[0-9:]{8}).*brief generated.*/\1 LLM/" \
    | awk 'NF>=4 && ($4=="FALLBACK"||$4=="LLM") {print $1" "$2" "$3" "$4}'
}
HISTORY_MAX_AGE=172800   # the old "--since 2 days ago": outcomes older than this leave the window

# 2026-10-20: lane accounting. src/common/brief_lane.py prints one
# "brief-lane: unit=X lane=long|hot|none wait=Ns" line per run. lane=hot = promoted to the
# second llama slot on long-runner contention; lane=none = no lane within the budget, the
# brief was SKIPPED (exit 75). Skips write no "brief generated" line, so before this the
# hourly 14:15-19:15Z ops-brief skips of 2026-10-20 never reached this monitor at all.
record_lanes() {  # $1 unit, $2 new journal chunk, $3 now -> appends to ${STATE_DIR}/<unit>.lanes
  local lanes="${STATE_DIR}/${1}.lanes"
  touch "$lanes"
  { cat "$lanes"
    grep -aE 'brief-lane: unit=[^ ]+ lane=(long|hot|none) ' "$2" \
      | sed -E "s/^([A-Z][a-z]{2}[[:space:]]+[0-9]{1,2}[[:space:]]+[0-9:]{8}).* lane=(long|hot|none) .*/${3} \1 \2/"
  } | awk -v cut=$(( $3 - HISTORY_MAX_AGE )) 'NF==5 && $1>=cut && !seen[$2" "$3" "$4]++' \
    | tail -n 200 > "${lanes}.tmp" && mv -f "${lanes}.tmp" "$lanes"
}
lane_counts() {  # $1 unit -> "long=a hot=b skipped=c" over the last 24 h
  awk -v cut=$(( $(date +%s) - 86400 )) '$1>=cut {n[$5]++} END {printf "long=%d hot=%d skipped=%d", n["long"], n["hot"], n["none"]}' \
    "${STATE_DIR}/${1}.lanes" 2>/dev/null || echo "long=0 hot=0 skipped=0"
}
outcomes_for() {  # history line: "<epoch read> Mon DD HH:MM:SS OUTCOME"
  local unit="$1" hist="${STATE_DIR}/${1}.outcomes" cur="${STATE_DIR}/${1}.cursor" new now
  now=$(date +%s)
  new=$(mktemp "${STATE_DIR}/.new.XXXXXX")
  jc_read "$new" "$cur" "2 days ago" 86400 --user -u "$unit" || true
  record_lanes "$unit" "$new" "$now"
  touch "$hist"
  { cat "$hist"; classify_lines < "$new" | sed "s/^/${now} /"; } \
    | awk -v cut=$((now - HISTORY_MAX_AGE)) '$1>=cut && !seen[$2" "$3" "$4]++' \
    | tail -n "$HISTORY_KEEP" > "${hist}.tmp" && mv -f "${hist}.tmp" "$hist"
  rm -f "$new"
  awk '{print $5}' "$hist" | tail -n "$WINDOW"
}

# 2026-10-14 (operator): alert when a brief FALLS BACK on its latest run, with a short narrative
# like the ops/EP trend briefs: which briefs fell back, and whether the trend is improving or
# worsening (fallbacks in the newer half of the window vs the older half). The old rule (>= half
# the window) kept paging for hours after the model had recovered.
trend_of() {  # $@ = outcomes oldest..newest -> "improving|worsening|steady (X of last N vs Y of prior M)"
  local -a w=("$@"); local n=${#w[@]} h=$(( ${#w[@]} / 2 )) old=0 new=0 i
  for ((i=0; i<h; i++)); do [ "${w[$i]}" = "FALLBACK" ] && old=$((old+1)); done
  for ((i=h; i<n; i++)); do [ "${w[$i]}" = "FALLBACK" ] && new=$((new+1)); done
  local dir=steady
  [ "$new" -lt "$old" ] && dir=improving
  [ "$new" -gt "$old" ] && dir=worsening
  echo "${dir} (${new} of last $((n-h)) vs ${old} of prior ${h})"
}
overall_bad=0; summary=""; fell=(); lines=""
for unit in "${UNITS[@]}"; do
  mapfile -t o < <(outcomes_for "$unit")
  n=${#o[@]}
  short="${unit#corporatetraveldc-}"
  if [ "$n" -eq 0 ]; then summary+="${unit}: no completed runs in window; "; continue; fi
  fb=0; for x in "${o[@]}"; do [ "$x" = "FALLBACK" ] && fb=$((fb+1)); done
  tr="$(trend_of "${o[@]}")"
  lc="$(lane_counts "$unit")"
  summary+="${unit}: last ${n}=[${o[*]}] fb=${fb}/${n} trend=${tr} lanes24h ${lc}; "
  if [ "${o[$((n-1))]}" = "FALLBACK" ]; then
    fell+=("$short")
    lines+="${short}: fell back on its latest run; ${fb} of last ${n} fell back; trend ${tr}; lanes 24h ${lc}."$'\n'
  else
    lines+="${short}: latest run used the model; ${fb} of last ${n} fell back; trend ${tr}; lanes 24h ${lc}."$'\n'
  fi
done
if [ "${#fell[@]}" -gt 0 ]; then
  overall_bad=1
  ntfy_alert "Brief fallback: ${fell[*]}" "${lines%$'\n'}"
fi

echo "brief-fallback-monitor: ${summary}"
if [ "$overall_bad" = "1" ]; then echo "brief-fallback-monitor: ALERT sent to ntfy/${ALERT_TOPIC}: ${fell[*]}"; else echo "brief-fallback-monitor: no brief fell back on its latest run -- no alert"; fi
exit 0
