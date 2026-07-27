#!/bin/bash
# scripts/ollama-keepwarm.sh
# Keeps OLLAMA_OSINT_MODEL (the model every brief skill -- ops_brief,
# ep_advance_brief, dispatch_desk_memo, osint_monitor, weekly_summary --
# actually requests) pinned resident in RAM so briefs never eat a cold-load
# penalty, without ever fighting ollama-governor.py's thermal SIGSTOP/SIGCONT.
#
# Why this exists (2026-07-26): ollama.service on this Pi cycles through
# long pause/resume windows all day -- confirmed live: paused at 76.0C,
# resumed at 67.75C, repeatedly, roughly every 10-70 minutes -- because of
# the known non-working fan (see ollama-governor.py, a deliberate fix so the
# thermal load doesn't cook the Pi5). Every time it resumes, Ollama starts
# with an empty model cache (/api/ps returns []), so the next brief to run
# pays a full cold-load penalty on top of whatever's left of its own
# processing time. This script closes that gap: it checks every 2 minutes
# whether Ollama is (a) actually running right now (not thermally paused)
# and (b) has no resident model, and if both are true, re-issues the warm
# keep_alive=-1 pin. It NEVER attempts to detect, wait out, or work around
# a thermal pause itself -- if Ollama's process is stopped (state T), this
# script does nothing and tries again next cycle. The governor's authority
# over Ollama's process state is absolute; this only ever adds a request
# AFTER Ollama is already running on its own.
#
# Usage:
#   ollama-keepwarm.sh          # normal run (called by the timer)
#   ollama-keepwarm.sh --status # print current state, no action

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/ollama-keepwarm"
LOG_FILE="${STATE_DIR}/keepwarm.log"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
OLLAMA_HOST="100.x.x.x:11434"   # confirmed bound here, not 127.0.0.1
WARM_TIMEOUT=30                     # seconds -- generous, a cold load can be slow on a Pi5

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
# ollama-governor.py SIGSTOPs the main "ollama serve" process on overheat.
# A stopped process shows state 'T' in ps. Never act while paused --
# governor authority is absolute here.
ollama_pid="$(pgrep -x ollama | head -1)"
ollama_state=""
if [[ -n "${ollama_pid}" ]]; then
    ollama_state="$(ps -o stat= -p "${ollama_pid}" 2>/dev/null | tr -d ' ')"
fi
thermally_paused=0
if [[ -z "${ollama_pid}" ]]; then
    thermally_paused=2   # process not found at all -- not the same as paused, but nothing to warm
elif [[ "${ollama_state}" == *T* ]]; then
    thermally_paused=1
fi

# --- Is the target model currently resident? ---------------------------------
resident="$(curl -sf --max-time 5 "http://${OLLAMA_HOST}/api/ps" 2>/dev/null)"
is_resident=0
if [[ -n "${resident}" ]] && grep -q "\"${MODEL}\"" <<< "${resident}"; then
    is_resident=1
fi

if [[ "${MODE}" == "status" ]]; then
    echo "ollama pid:       ${ollama_pid:-none}"
    echo "ollama ps state:  ${ollama_state:-unknown}"
    echo "thermally_paused: ${thermally_paused} (0=running 1=paused 2=not-found)"
    echo "target model:     ${MODEL}"
    echo "resident now:     ${is_resident}"
    exit 0
fi

if (( thermally_paused != 0 )); then
    log "info" "skip -- ollama not running normally (pid=${ollama_pid:-none} state=${ollama_state:-n/a}), leaving governor alone"
    exit 0
fi

if (( is_resident == 1 )); then
    log "info" "ok -- ${MODEL} already resident"
    exit 0
fi

log "warn" "${MODEL} not resident and ollama is running -- re-pinning with keep_alive=-1"
warm_out="$(curl -sf --max-time "${WARM_TIMEOUT}" "http://${OLLAMA_HOST}/api/generate" \
    -d "{\"model\":\"${MODEL}\",\"prompt\":\"warm\",\"stream\":false,\"keep_alive\":-1}" 2>&1)"
rc=$?
if (( rc == 0 )); then
    log "info" "re-pinned ${MODEL} successfully"
else
    log "error" "warm-up call failed (rc=${rc}): ${warm_out:0:200}"
fi
