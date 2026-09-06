#!/usr/bin/env bash
# maintenance-window-off.sh — restores corporatetraveldc-llama-chat.service
# to its committed CPUWeight=9000 baseline after a maintenance window
# (see maintenance-window-on.sh). Always safe to call even if the window
# was never actually engaged -- it just re-asserts the tracked baseline.
set -euo pipefail

UNIT="corporatetraveldc-llama-chat.service"
BASELINE_WEIGHT=9000

echo "[maintenance-window] restoring ${UNIT} CPUWeight -> ${BASELINE_WEIGHT}"
systemctl --user set-property --runtime "${UNIT}" "CPUWeight=${BASELINE_WEIGHT}"
