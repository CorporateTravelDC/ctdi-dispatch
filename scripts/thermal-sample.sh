#!/bin/bash
# scripts/thermal-sample.sh
# Lightweight CPU-temp + ollama-state sampler, 5min cadence, for the
# 2026-08-03 prewarm-fix live comparison test. Appends one CSV row per run:
# timestamp,temp_c,ollama_pid_state,resident_models
set -uo pipefail
STATE_DIR="/var/lib/corporatetraveldc/ollama-keepwarm"
OUT="${STATE_DIR}/thermal-samples.csv"
mkdir -p "${STATE_DIR}"
[[ -f "${OUT}" ]] || echo "timestamp,temp_c,ollama_state,resident" > "${OUT}"

ts=$(date '+%Y-%m-%d %H:%M:%S')
temp_raw=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo "0")
temp_c=$(awk "BEGIN{printf \"%.1f\", ${temp_raw}/1000}")
pid=$(pgrep -x ollama | head -1)
state=$(ps -o stat= -p "${pid}" 2>/dev/null | tr -d ' ')
resident=$(curl -sf --max-time 4 http://100.x.x.x:11434/api/ps 2>/dev/null | grep -oE '"name":"[^"]+"' | tr '\n' '|' )
echo "${ts},${temp_c},${state:-none},\"${resident:-}\"" >> "${OUT}"
