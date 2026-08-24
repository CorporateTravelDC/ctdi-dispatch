#!/bin/bash
# check-pat-expiry.sh — fires ntfy alert if GitHub PAT expires within WARN_DAYS
# Reads expiry from first line of ~/.secrets/github.token ("Valid thru MMM DD, YYYY -- ...")
# Cron: runs daily via systemd timer or cron.d

WARN_DAYS=5
TOKEN_FILE="${HOME}/.secrets/github.token"
NTFY_URL="http://127.0.0.1:2586"
NTFY_TOPIC="dispatch-alerts"
NTFY_TOKEN_FILE="${HOME}/.secrets/ntfy.token"

if [[ ! -f "$TOKEN_FILE" ]]; then
    echo "PAT file not found: $TOKEN_FILE" >&2
    exit 1
fi

FIRST_LINE=$(head -1 "$TOKEN_FILE")

# Parse "Valid thru MMM DD, YYYY" — strip anything after " --"
if [[ "$FIRST_LINE" =~ Valid\ thru\ ([A-Za-z]+\ [0-9]+,\ [0-9]{4}) ]]; then
    EXPIRY_STR="${BASH_REMATCH[1]}"
    EXPIRY_EPOCH=$(date -d "$EXPIRY_STR" +%s 2>/dev/null)
    if [[ -z "$EXPIRY_EPOCH" ]]; then
        echo "date parse failed for: $EXPIRY_STR" >&2
        exit 0
    fi
else
    echo "Could not parse expiry from: $FIRST_LINE" >&2
    exit 0
fi

NOW_EPOCH=$(date +%s)
DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))

if [[ $DAYS_LEFT -le $WARN_DAYS ]]; then
    NTFY_TOKEN=$(cat "$NTFY_TOKEN_FILE" 2>/dev/null | tr -d '[:space:]')
    MSG="GitHub PAT expires in ${DAYS_LEFT} day(s) (${EXPIRY_STR}). Rotate at: https://github.com/settings/personal-access-tokens"
    PRIO=4
    [[ $DAYS_LEFT -le 1 ]] && PRIO=5

    curl -s -X POST "${NTFY_URL}/${NTFY_TOPIC}" \
        -H "Authorization: Bearer ${NTFY_TOKEN}" \
        -H "Title: GitHub PAT Expiring Soon" \
        -H "Priority: ${PRIO}" \
        -H "Tags: warning,key" \
        -d "$MSG" > /dev/null

    echo "Alert sent: PAT expires in ${DAYS_LEFT} day(s)"
else
    echo "PAT OK — ${DAYS_LEFT} day(s) remaining until ${EXPIRY_STR}"
fi
