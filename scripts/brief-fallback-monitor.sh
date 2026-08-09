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
set -uo pipefail

ENV_FILE=/etc/corporatetraveldc/dispatch.env
SECRETS_FILE=/etc/corporatetraveldc/dispatch-secrets.env
THRESHOLD="${BRIEF_FALLBACK_ALERT_THRESHOLD:-3}"   # trailing consecutive fallbacks -> alert
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
  [ -n "$NTFY_TOKEN" ] && auth=(-H "Authorization: Bearer ${NTFY_TOKEN}")
  curl -s -m 10 "${auth[@]}" \
    -H "Title: $1" -H "Priority: max" -H "Tags: rotating_light,robot" \
    -d "$2" "${NTFY_BASE}/${ALERT_TOPIC}" >/dev/null 2>&1 || true
}

# One outcome per run (dedupe the journal's double-logging by timestamp), newest last.
outcomes_for() {
  local unit="$1"
  journalctl --user -u "$unit" --no-pager -n 1000 --since "2 days ago" 2>/dev/null \
    | grep -aE 'brief generated' \
    | sed -E "s/^([A-Z][a-z]{2}[[:space:]]+[0-9]{1,2}[[:space:]]+[0-9:]{8}).*[Dd]eterministic.*/\1 FALLBACK/; s/^([A-Z][a-z]{2}[[:space:]]+[0-9]{1,2}[[:space:]]+[0-9:]{8}).*brief generated.*/\1 LLM/" \
    | awk 'NF>=4 && ($4=="FALLBACK"||$4=="LLM") && !seen[$1" "$2" "$3]++ {print $4}' \
    | tail -n "$WINDOW"
}

overall_bad=0; summary=""
for unit in "${UNITS[@]}"; do
  mapfile -t o < <(outcomes_for "$unit")
  n=${#o[@]}
  if [ "$n" -eq 0 ]; then summary+="${unit}: no completed runs in window; "; continue; fi
  consec=0
  for ((i=n-1; i>=0; i--)); do [ "${o[$i]}" = "FALLBACK" ] && consec=$((consec+1)) || break; done
  fb=0; for x in "${o[@]}"; do [ "$x" = "FALLBACK" ] && fb=$((fb+1)); done
  summary+="${unit}: last ${n}=[${o[*]}] consec_fb=${consec} fb=${fb}/${n}; "
  if [ "$consec" -ge "$THRESHOLD" ] || { [ "$n" -ge "$WINDOW" ] && [ "$fb" -ge $(((WINDOW + 1) / 2)) ]; }; then
    overall_bad=1
    ntfy_alert "⛔ BRIEF LLM DOWN: ${unit}" \
"${unit} briefs are falling back to DETERMINISTIC — LLM generation is failing. Last ${n} runs: ${o[*]}. Consecutive fallbacks: ${consec} (threshold ${THRESHOLD}). This is the gemma3-SWA / Ollama-timeout failure class; check the brief model + Ollama now. (memory: brief-ollama-gemma3-swa-fallback)"
  fi
done

echo "brief-fallback-monitor: ${summary}"
if [ "$overall_bad" = "1" ]; then echo "brief-fallback-monitor: MAX-PRIORITY ALERT sent to ntfy/${ALERT_TOPIC}"; else echo "brief-fallback-monitor: healthy — no alert"; fi
exit 0
