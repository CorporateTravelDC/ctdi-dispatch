#!/bin/bash
# scripts/ollama-prewarm.sh
# Replaces ollama-keepwarm.sh's "keep it resident forever" design with a
# just-in-time model: fires ~2 minutes before each real Ollama-consuming
# brief (ops-brief :00, ep-advance :30 -- see the paired timer's OnCalendar
# entries) and, if the model isn't already resident, warms it with a
# BOUNDED keep_alive so it's ready by the time the real brief runs, then
# lets it fall idle and unload naturally afterward via ollama.service's
# own OLLAMA_KEEP_ALIVE=10m default.
#
# Why this exists (2026-07-28): keep_alive=-1 (the old keepwarm.sh design)
# pins the model resident permanently, which was the actual driver of
# sustained swap pressure (llama-server holding ~3.2GB RSS for its entire
# uptime, confirmed live). The operator's own testing showed the cold-load
# penalty itself is tolerable -- the earlier "wonky timeouts" complaint was
# from before the pause-aware wait_then_budget()/retry fix (2026-07-27),
# which already gives brief scripts a generous 900s internal / 950s
# subprocess timeout budget, comfortably covering a cold load. So there's
# no need to keep the model loaded 24/7 -- just make sure it's warm a
# couple minutes before it's actually needed.
#
# This script does NOT fight ollama-governor.py's thermal SIGSTOP/SIGCONT
# any more than the old keepwarm.sh did -- same "never act while paused"
# guard, carried over unchanged.
#
# Usage:
#   ollama-prewarm.sh          # normal run (called by the timer, ~2min before a brief)
#   ollama-prewarm.sh --status # print current state, no action

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/ollama-keepwarm"
LOG_FILE="${STATE_DIR}/prewarm.log"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
OLLAMA_HOST="100.x.x.x:11434"
WARM_TIMEOUT=30
PREWARM_KEEP_ALIVE="15m"   # bounded -- covers a ~6min brief run plus buffer, then unloads

mkdir -p "${STATE_DIR}"

read_env_var() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    grep -m1 "^${key}=" "${file}" 2>/dev/null | cut -d'=' -f2-
}

MODEL="$(read_env_var OLLAMA_OSINT_MODEL "${ENV_FILE}")"
MODEL="${MODEL:-corporatetraveldc-pi5-osint}"

log() {
    local level="$1"; shift
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${ts}] [${level^^}] $*" >> "${LOG_FILE}" 2>/dev/null
    echo "[${ts}] [${level^^}] $*"
}

MODE="run"
[[ "${1:-}" == "--status" ]] && MODE="status"

# --- Is Ollama's process currently thermally paused? ------------------------
# Same guard as the old keepwarm.sh -- governor authority is absolute.
ollama_pid="$(pgrep -x ollama | head -1)"
ollama_state=""
if [[ -n "${ollama_pid}" ]]; then
    ollama_state="$(ps -o stat= -p "${ollama_pid}" 2>/dev/null | tr -d ' ')"
fi
thermally_paused=0
if [[ -z "${ollama_pid}" ]]; then
    thermally_paused=2
elif [[ "${ollama_state}" == *T* ]]; then
    thermally_paused=1
fi

# --- Is the target model currently resident? ---------------------------------
# Same fixed regex as keepwarm.sh (2026-07-27 fix carried over) -- /api/ps
# always returns name:tag, never a bare name.
resident="$(curl -sf --max-time 5 "http://${OLLAMA_HOST}/api/ps" 2>/dev/null)"
is_resident=0
if [[ -n "${resident}" ]] && grep -qE "\"name\":\"${MODEL}(:[A-Za-z0-9_.-]+)?\"" <<< "${resident}"; then
    is_resident=1
fi

if [[ "${MODE}" == "status" ]]; then
    echo "ollama pid:       ${ollama_pid:-none}"
    echo "ollama ps state:  ${ollama_state:-unknown}"
    echo "thermally_paused: ${thermally_paused} (0=running 1=paused 2=not-found)"
    echo "target model:     ${MODEL}"
    echo "resident now:     ${is_resident}"
    echo "prewarm keep_alive: ${PREWARM_KEEP_ALIVE}"
    exit 0
fi

if (( thermally_paused != 0 )); then
    log "info" "skip -- ollama not running normally (pid=${ollama_pid:-none} state=${ollama_state:-n/a}), leaving governor alone"
    exit 0
fi

if (( is_resident == 1 )); then
    log "info" "ok -- ${MODEL} already resident, nothing to prewarm"
    exit 0
fi

log "info" "${MODEL} not resident ahead of scheduled brief -- prewarming with keep_alive=${PREWARM_KEEP_ALIVE}"
warm_out="$(curl -sf --max-time "${WARM_TIMEOUT}" "http://${OLLAMA_HOST}/api/generate" \
    -d "{\"model\":\"${MODEL}\",\"prompt\":\"warm\",\"stream\":false,\"keep_alive\":\"${PREWARM_KEEP_ALIVE}\",\"options\":{\"num_predict\":4}}" 2>&1)"
rc=$?
if (( rc == 0 )); then
    log "info" "prewarmed ${MODEL} successfully (bounded ${PREWARM_KEEP_ALIVE}, will idle-unload after last use)"
else
    log "error" "prewarm call failed (rc=${rc}): ${warm_out:0:200}"
fi
