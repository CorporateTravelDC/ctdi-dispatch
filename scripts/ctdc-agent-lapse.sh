#!/usr/bin/env bash
# scripts/ctdc-agent-lapse.sh -- the operator's SSH control for the agent-lapse sentinel (2026-11-18).
#
#   ctdc-agent-lapse.sh status
#   ctdc-agent-lapse.sh dismiss --account ACCOUNT [--for 24h|90m|3d] --reason "TEXT"
#
# Operator account only: a dismissal suppresses the 6/12/24 h escalation for that account until the
# window ends or the account shows activity again, and it is recorded (who, when, via=ssh, reason).
# The other way to dismiss is the operator console (/console), behind its normal signed sign-in.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPERATOR="${CTDC_OPERATOR_ACCOUNT:-corporatetraveldc}"
ME="$(id -un)"
if [[ "$ME" != "$OPERATOR" ]]; then
  echo "ctdc-agent-lapse: run as ${OPERATOR} (you are ${ME}); nothing changed" >&2
  exit 77
fi
cmd="${1:-status}"; shift || true
case "$cmd" in
  status)  exec python3 "$REPO_DIR/scripts/platform-sentinel.py" agent-lapse status ;;
  dismiss) exec python3 "$REPO_DIR/scripts/platform-sentinel.py" agent-lapse dismiss --by "$ME" --via ssh "$@" ;;
  *) echo "usage: ctdc-agent-lapse.sh status | dismiss --account X [--for 24h] --reason TEXT" >&2; exit 64 ;;
esac
