#!/usr/bin/env bash
# Operator deployment/weekly entrypoint: vault editions and verified local delivery.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
bash scripts/verify-manifest.sh
request=/var/lib/ctdc-agent-pamphlets/refresh-request
# No sudo and no arbitrary root command: writing this fixed marker only asks the
# installed root service to independently verify and deliver the signed source.
epoch=$(python3 scripts/agent-pamphlet-request.py "$request")
bash scripts/agent-pamphlet-weekly-refresh.sh
for attempt in {1..90}; do
    if python3 scripts/agent-pamphlet-install.py --check "$epoch" 2>/dev/null; then
        echo "pamphlets: vault editions published; local receipts verified"
        exit 0
    fi
    sleep 2
done
echo "pamphlets: local delivery did not verify; inspect corporatetraveldc-agent-pamphlet-install.service" >&2
exit 1
