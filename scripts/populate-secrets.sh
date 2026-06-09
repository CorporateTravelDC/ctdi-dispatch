#!/bin/bash
# populate-secrets.sh
# Reads ~/.secrets/{name}.{token,key} files and writes values into
# /etc/corporatetraveldc/dispatch-secrets.env.
#
# Convention: ~/.secrets/{logicalname}.token or ~/.secrets/{logicalname}.key
# Run as corporatetraveldc after adding or rotating any credential.
# Safe to re-run -- overwrites only the keys it knows about.
#
# Usage:
#   bash populate-secrets.sh [--dry-run]

SECRETS_DIR="${HOME}/.secrets"
ENV_FILE="/etc/corporatetraveldc/dispatch-secrets.env"
DRY_RUN=0
[ "$1" = "--dry-run" ] && DRY_RUN=1

read_secret() {
    local name="$1"
    local file=""
    # Try .token first, then .key
    for ext in token key; do
        f="${SECRETS_DIR}/${name}.${ext}"
        [ -f "$f" ] && file="$f" && break
    done
    [ -z "$file" ] && return 1
    cat "$f" | tr -d '[:space:]'
}

set_env() {
    local key="$1"
    local val="$2"
    if [ -z "$val" ]; then
        echo "  [SKIP] ${key} -- secret file not found in ${SECRETS_DIR}"
        return
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [DRY]  ${key}=<${#val} chars>"
        return
    fi
    # Replace existing line or append
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sudo sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
        echo "${key}=${val}" | sudo tee -a "$ENV_FILE" > /dev/null
    fi
    echo "  [OK]   ${key} set (${#val} chars)"
}

echo "=== populate-secrets.sh ==="
echo "Secrets dir: ${SECRETS_DIR}"
echo "Target:      ${ENV_FILE}"
[ "$DRY_RUN" -eq 1 ] && echo "Mode:        DRY RUN"
echo ""

# -- acarsdrama Jumpseat (VDL2/ACARS/HFDL external source)
set_env "ACARSDRAMA_JUMPSEAT_TOKEN" "$(read_secret acarsdrama)"

# -- airframes.io (secondary external source)
set_env "AIRFRAMES_TOKEN" "$(read_secret airframes)"

# -- MarineTraffic API key (AIS fallback)
set_env "MARINETRAFFIC_API_KEY" "$(read_secret marinetraffic)"

# -- FAA NOTAM API
set_env "FAA_NOTAM_API_KEY" "$(read_secret faa-notam)"

# -- ntfy auth token
set_env "NTFY_TOKEN" "$(read_secret ntfy)"

# -- Anthropic API key
set_env "ANTHROPIC_API_KEY" "$(read_secret anthropic)"

# -- AIS feeder keys
set_env "AIS_AISHUB_ID"          "$(read_secret aishub)"
set_env "AIS_MARINETRAFFIC_KEY"  "$(read_secret marinetraffic)"
set_env "AIS_VESSELFI_KEY"       "$(read_secret vesselfi)"

echo ""
echo "=== Done ==="
echo "Restart containers to pick up changes:"
echo "  systemctl --user restart corporatetraveldc-runner.service"
echo "  systemctl --user restart corporatetraveldc-web.service"
echo "  systemctl --user restart corporatetraveldc-poller.service"
