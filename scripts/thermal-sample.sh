#!/bin/bash
# scripts/thermal-sample.sh
# Lightweight CPU-temp + fan + model-server + load sampler, 5min cadence.
# Originally built for the 2026-08-03 prewarm-fix comparison test; now also
# the longitudinal record used to judge contention inside the 23:00-05:00
# maintenance window. Appends one CSV row per run:
# timestamp,temp_c,llama_state,resident,fan_rpm,fan_pwm_pct,load1,load5
set -uo pipefail
# Legacy path, kept deliberately: renaming it to match the llama.cpp cutover
# would orphan every historical sample, and continuity of this CSV is the
# entire reason it exists. The directory name is the only Ollama artifact
# left here.
STATE_DIR="/var/lib/corporatetraveldc/ollama-keepwarm"
OUT="${STATE_DIR}/thermal-samples.csv"
mkdir -p "${STATE_DIR}"

HEADER="timestamp,temp_c,llama_state,resident,fan_rpm,fan_pwm_pct,load1,load5"
# Schema evolves in-line: when HEADER changes, append the new one rather than
# rewriting history, so rows above the transition stay readable as what they
# actually were. Fan columns arrived 2026-08-10; load columns and the
# ollama->llama rename arrived 2026-09-21. `grep -qxF` (whole-line, fixed
# string) makes this idempotent across any number of future changes -- the
# earlier per-column checks broke as soon as a second transition existed,
# since the newest header is mid-file, not line 1.
if [[ ! -f "${OUT}" ]]; then
  echo "${HEADER}" > "${OUT}"
elif ! grep -qxF "${HEADER}" "${OUT}"; then
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
# 2026-09-21: repointed from Ollama to llama.cpp. Ollama was retired
# 2026-08-27 and nothing has listened on :11434 since, so `pgrep -x ollama`
# and the /api/ps probe had both been recording empty for ~a month -- the
# columns were live but meaningless. Now reads the real resident model from
# corporatetraveldc-llama.service (:8093).
#
# pgrep -f, not -x: the llama.cpp binary still lives at the legacy path
# /usr/local/lib/ollama/llama-server, so an exact-name match is brittle
# while a full-command match is not.
pid=$(pgrep -f "llama-server" | head -1)
state=$(ps -o stat= -p "${pid}" 2>/dev/null | tr -d ' ')
# /v1/models is the llama.cpp equivalent of Ollama's /api/ps. Only one model
# is ever resident by design (see corporatetraveldc-llama.service's "one
# model weight EVER loaded" directive), so this returns exactly one entry --
# basename'd to keep the CSV column short and diffable across model swaps.
resident=$(curl -sf --max-time 4 http://100.x.x.x:8093/v1/models 2>/dev/null \
  | grep -oE '"id":"[^"]+"' | sed 's|.*/||; s|"$||' | tr '\n' '|' )
read -r load1 load5 _ < /proc/loadavg
echo "${ts},${temp_c},${state:-none},\"${resident:-}\",${fan_rpm},${fan_pwm_pct},${load1},${load5}" >> "${OUT}"
