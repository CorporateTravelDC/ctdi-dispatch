#!/bin/bash
# scripts/ollama-prewarm.sh
# Replaces ollama-keepwarm.sh's "keep it resident forever" design with a
# just-in-time model: fires ~2-3 minutes before each real Ollama-consuming
# skill run and, if the model isn't already resident, warms it with a
# BOUNDED keep_alive so it's ready by the time the real skill runs, then
# lets it fall idle and unload naturally afterward via ollama.service's
# own OLLAMA_KEEP_ALIVE=10m default.
#
# 2026-08-03 FIX: this script previously always warmed OLLAMA_OSINT_MODEL
# (corporatetraveldc-pi5-osint) regardless of which skill was actually about
# to fire. That model predates the 2026-08-02 per-skill Modelfile migration
# and is NOT the model ops-brief/ep-advance/etc. actually use -- so every
# prewarm was warming a model nothing was about to call, while the real
# skill still cold-loaded its own model from scratch every single run.
# Confirmed via prewarm.log: 100% failure (rc=28, curl timeout) for the
# entire review window, because the wrong-target bug was compounded by a
# WARM_TIMEOUT that was already too tight for a REAL cold load (measured:
# 27.8s total / 20.5s load_duration for corporatetraveldc-pi5-ops-brief,
# CPU-only inference, no GPU) once the box is running under any thermal
# governor throttling.
#
# Now: model is a required argument, supplied by each skill's own prewarm
# service/timer pair (one per skill, not one generic timer firing twice
# with the same hardcoded target).
#
# This script does NOT fight ollama-governor.py's thermal SIGSTOP/SIGCONT
# any more than the old keepwarm.sh did -- same "never act while paused"
# guard, carried over unchanged.
#
# Usage:
#   ollama-prewarm.sh <model-name>            # normal run (called by a skill's timer, ~2-3min before it fires)
#   ollama-prewarm.sh <model-name> --status   # print current state for that model, no action

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/ollama-keepwarm"
LOG_FILE="${STATE_DIR}/prewarm.log"
OLLAMA_HOST="100.x.x.x:11434"
WARM_TIMEOUT=60            # was 30 -- real measured cold load is 20-28s unthrottled;
                           # this Pi spends >50% of samples in tier-2 throttling
                           # (see thermal_baseline_argon_case memory), which routinely
                           # pushed real loads past the old 30s ceiling -> rc=28 every time.
PREWARM_KEEP_ALIVE="15m"   # bounded -- covers a several-minute brief run plus buffer, then unloads

mkdir -p "${STATE_DIR}"

MODEL="${1:-}"
if [[ -z "${MODEL}" || "${MODEL}" == "--status" ]]; then
    echo "usage: ollama-prewarm.sh <model-name> [--status]" >&2
    echo "  no model argument given -- refusing to guess a target (this was the 2026-08-03 bug)" >&2
    exit 2
fi
shift

log() {
    local level="$1"; shift
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${ts}] [${level^^}] [${MODEL}] $*" >> "${LOG_FILE}" 2>/dev/null
    echo "[${ts}] [${level^^}] [${MODEL}] $*"
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
    echo "warm timeout:     ${WARM_TIMEOUT}s"
    echo "prewarm keep_alive: ${PREWARM_KEEP_ALIVE}"
    exit 0
fi

if (( thermally_paused != 0 )); then
    log "info" "skip -- ollama not running normally (pid=${ollama_pid:-none} state=${ollama_state:-n/a}), leaving governor alone"
    exit 0
fi

if (( is_resident == 1 )); then
    log "info" "ok -- already resident, nothing to prewarm"
    exit 0
fi

log "info" "not resident ahead of scheduled skill run -- prewarming with keep_alive=${PREWARM_KEEP_ALIVE} timeout=${WARM_TIMEOUT}s"
start_ts=$(date +%s.%N)
warm_out="$(curl -sf --max-time "${WARM_TIMEOUT}" "http://${OLLAMA_HOST}/api/generate" \
    -d "{\"model\":\"${MODEL}\",\"prompt\":\"warm\",\"stream\":false,\"keep_alive\":\"${PREWARM_KEEP_ALIVE}\",\"options\":{\"num_predict\":4}}" 2>&1)"
rc=$?
end_ts=$(date +%s.%N)
elapsed=$(awk "BEGIN{printf \"%.1f\", ${end_ts}-${start_ts}}")
if (( rc == 0 )); then
    log "info" "prewarmed successfully in ${elapsed}s (bounded ${PREWARM_KEEP_ALIVE}, will idle-unload after last use)"
else
    log "error" "prewarm call failed after ${elapsed}s (rc=${rc}): ${warm_out:0:200}"
fi
