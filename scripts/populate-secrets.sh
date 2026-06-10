#!/bin/bash
# populate-secrets.sh
# Reads ~/.secrets/{name}.{token,key} files and writes values into
# /etc/corporatetraveldc/dispatch-secrets.env.
#
# Convention (enforced):
#   {service}.token  = feeder sharing/auth key for that service's feeder daemon
#   {service}.key    = REST API key for that same service
# When a service has both, use read_secret with explicit extension (2nd arg).
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
    # Usage: read_secret <name> [token|key]
    # If extension is supplied, reads that exact file.
    # If omitted, tries .token then .key (legacy fallback).
    local name="$1"
    local ext_hint="$2"
    if [ -n "$ext_hint" ]; then
        local f="${SECRETS_DIR}/${name}.${ext_hint}"
        [ -f "$f" ] || return 1
        cat "$f" | tr -d '[:space:]'
        return
    fi
    local file=""
    for ext in token key; do
        local f="${SECRETS_DIR}/${name}.${ext}"
        [ -f "$f" ] && file="$f" && break
    done
    [ -z "$file" ] && return 1
    cat "$file" | tr -d '[:space:]'
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

# -- acarsdrama feeder sharing key
# ~/.secrets/acarsdrama.token  ->  ACARSDRAMA_FEEDER_KEY
set_env "ACARSDRAMA_FEEDER_KEY" "$(read_secret acarsdrama token)"

# -- acarsdrama Jumpseat privileged API key (VDL2/ACARS/HFDL enrichment)
# ~/.secrets/jumpseat.token  ->  ACARSDRAMA_JUMPSEAT_TOKEN
set_env "ACARSDRAMA_JUMPSEAT_TOKEN" "$(read_secret jumpseat token)"

# -- airframes.io (secondary external fallback for VDL2/ACARS/HFDL)
set_env "AIRFRAMES_TOKEN" "$(read_secret airframes)"

# -- MarineTraffic API key (AIS fallback)
set_env "MARINETRAFFIC_API_KEY" "$(read_secret marinetraffic)"

# -- FAA NOTAM API
set_env "FAA_NOTAM_API_KEY" "$(read_secret faa-notam)"

# -- ntfy auth token
set_env "NTFY_TOKEN" "$(read_secret ntfy)"

# -- Anthropic API key
set_env "ANTHROPIC_API_KEY" "$(read_secret anthropic)"

# -- FlightAware feeder sharing key (piaware station key)
# ~/.secrets/flightaware.token  ->  FLIGHTAWARE_FEEDER_KEY
set_env "FLIGHTAWARE_FEEDER_KEY" "$(read_secret flightaware token)"

# -- FlightAware AeroAPI key (REST API, watchlist enrichment, flight data)
# ~/.secrets/flightaware.key  ->  FLIGHTAWARE_AEROAPI_KEY
set_env "FLIGHTAWARE_AEROAPI_KEY" "$(read_secret flightaware key)"

# -- AirNav RadarBox feeder sharing key
# ~/.secrets/airnavradar.token  ->  AIRNAVRADAR_SHARING_KEY
set_env "AIRNAVRADAR_SHARING_KEY" "$(read_secret airnavradar)"

# -- FlightRadar24 feeder sharing key
# ~/.secrets/flightradar24.token  ->  FR24_SHARING_KEY
set_env "FR24_SHARING_KEY" "$(read_secret flightradar24)"

# -- PlaneFinder feeder sharing key
# ~/.secrets/planefinder.token  ->  PLANEFINDER_SHARING_KEY
set_env "PLANEFINDER_SHARING_KEY" "$(read_secret planefinder)"

# -- AIS feeder keys
set_env "AIS_AISHUB_ID"          "$(read_secret aishub)"
set_env "AIS_MARINETRAFFIC_KEY"  "$(read_secret marinetraffic)"
set_env "AIS_VESSELFI_KEY"       "$(read_secret vesselfi)"

echo ""
echo "=== Done ==="
echo "Restart containers to pick up changes:"
echo "  systemctl --user restart corporatetraveldc-runner.service"
echo "  systemctl --user restart corporatetraveldc-web.service"
echo "  systemctl --user restart corporatetraveldc-web.service"
echo "  systemctl --user restart corporatetraveldc-poller.service"
echo "  systemctl --user restart corporatetraveldc-ingest.service"
echo "  systemctl --user restart corporatetraveldc-fr24feed.service"
echo "  systemctl --user restart corporatetraveldc-airnavradar.service"
echo "  systemctl --user restart corporatetraveldc-planefinder.service"
