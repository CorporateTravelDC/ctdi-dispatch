#!/usr/bin/env bash
# maintenance-window-on.sh — temporarily de-prioritizes the llama-chat tier
# so a competing CPU-heavy job (container build, bulk import, etc.) actually
# gets scheduled instead of losing every contention round.
#
# 2026-09-01: corporatetraveldc-llama-chat.service runs CPUWeight=9000 (see
# its unit file -- "a human is waiting on the reply, same tier as hot").
# Default cgroup CPUWeight is 100, so under contention an unweighted build
# process gets ~1% of CPU (100 / (9000+100)) against it. That contention,
# not a broken network, was the real cause of pip read-timeouts during
# `build-images.sh` runs while llama-chat was continuously busy.
#
# --runtime makes this a transient override (not written to disk / not
# tracked in the unit file) so it can never accidentally survive a reboot
# or drift out of sync with the committed CPUWeight=9000 baseline.
#
# Usage: scripts/maintenance-window-on.sh [suppressed-weight]
set -euo pipefail

UNIT="corporatetraveldc-llama-chat.service"
SUPPRESSED_WEIGHT="${1:-50}"

echo "[maintenance-window] suppressing ${UNIT} CPUWeight -> ${SUPPRESSED_WEIGHT} (runtime-only)"
systemctl --user set-property --runtime "${UNIT}" "CPUWeight=${SUPPRESSED_WEIGHT}"
