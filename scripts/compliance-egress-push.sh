#!/usr/bin/env bash
# compliance-egress-push.sh -- runs common.compliance_egress.push_pending_audit_events()
# inside the already-running poller container (has the full app image,
# httpx, and DB access). No-ops immediately (logs nothing, exits 0) unless
# COMPLIANCE_HOOK_ENABLED=true is set -- see common/compliance_egress.py
# for the full config contract. Built 2026-08-03.
set -euo pipefail

LOG_FILE="/home/corporatetraveldc/.local/share/corporatetraveldc/compliance-egress-push.log"
mkdir -p "$(dirname "${LOG_FILE}")"

ts="$(date '+%Y-%m-%d %H:%M:%S')"
out="$(podman exec systemd-corporatetraveldc-poller python3 -m common.compliance_egress 2>&1)" || {
    echo "[${ts}] [ERROR] compliance-egress-push failed: ${out}" >> "${LOG_FILE}"
    exit 0   # never fail the timer over this -- see module docstring
}
echo "[${ts}] [INFO] ${out}" >> "${LOG_FILE}"
