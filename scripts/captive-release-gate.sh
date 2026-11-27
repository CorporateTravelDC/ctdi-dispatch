#!/usr/bin/env bash
# scripts/captive-release-gate.sh -- the signed captive-portal release for the DNS leak watch (2026-11-27, operator).
# Thin wrapper: loads the dispatch env verbatim and runs scripts/captive-release-gate.py (see its docstring).
#
#   scripts/captive-release-gate.sh request --host <host> --reason "hotel Wi-Fi captive portal, checked"
#   scripts/approve.sh allow <id>                       # the human signature; nothing else releases the hold
#   scripts/captive-release-gate.sh check --host <host> --since <epoch>   # what the root run calls (RELEASE_APPROVAL_CMD)
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
exec "${REPO_DIR}/scripts/with-dispatch-env.sh" env PYTHONPATH="${REPO_DIR}/src" \
  python3 "${REPO_DIR}/scripts/captive-release-gate.py" "$@"
