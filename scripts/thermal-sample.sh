#!/bin/bash
# scripts/thermal-sample.sh
# Lightweight CPU-temp + fan + ollama-state sampler, 5min cadence, for the
# 2026-08-03 prewarm-fix live comparison test. Appends one CSV row per run:
# timestamp,temp_c,ollama_pid_state,resident_models,fan_rpm,fan_pwm_pct
set -uo pipefail
STATE_DIR="/var/lib/corporatetraveldc/ollama-keepwarm"
OUT="${STATE_DIR}/thermal-samples.csv"
mkdir -p "${STATE_DIR}"

HEADER="timestamp,temp_c,ollama_state,resident,fan_rpm,fan_pwm_pct"
if [[ ! -f "${OUT}" ]]; then
  echo "${HEADER}" > "${OUT}"
elif ! head -1 "${OUT}" | grep -q "fan_rpm"; then
  # Schema gained fan columns 2026-08-10 -- mark the transition in-line
  # rather than rewriting history; rows above this line predate fan logging.
  echo "${HEADER}" >> "${OUT}"
fi

# Resolve the real cooling fan by hwmon NAME ("pwmfan"), not a fixed
# hwmonN path -- numbering shifts once the dead gpio-fan overlay (Argon
# ONE leftover, always-max-duty, no physical fan attached) is removed
# from /boot/config.txt and the box reboots.
fan_rpm=""
fan_pwm_pct=""
for hw in /sys/class/hwmon/hwmon*; do
  if [[ "$(cat "${hw}/name" 2>/dev/null)" == "pwmfan" ]]; then
    fan_rpm=$(cat "${hw}/fan1_input" 2>/dev/null || echo "")
    pwm_raw=$(cat "${hw}/pwm1" 2>/dev/null || echo "")
    if [[ -n "${pwm_raw}" ]]; then
      fan_pwm_pct=$(awk "BEGIN{printf \"%.0f\", ${pwm_raw}/255*100}")
    fi
    break
  fi
done

ts=$(date '+%Y-%m-%d %H:%M:%S')
temp_raw=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo "0")
temp_c=$(awk "BEGIN{printf \"%.1f\", ${temp_raw}/1000}")
pid=$(pgrep -x ollama | head -1)
state=$(ps -o stat= -p "${pid}" 2>/dev/null | tr -d ' ')
resident=$(curl -sf --max-time 4 http://100.x.x.x:11434/api/ps 2>/dev/null | grep -oE '"name":"[^"]+"' | tr '\n' '|' )
echo "${ts},${temp_c},${state:-none},\"${resident:-}\",${fan_rpm},${fan_pwm_pct}" >> "${OUT}"
