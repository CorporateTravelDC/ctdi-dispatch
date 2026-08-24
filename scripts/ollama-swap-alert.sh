#!/bin/bash
# scripts/ollama-swap-alert.sh
#
# 2026-07-28: parity gap found the same morning as the boot-storm crashes --
# every dispatch container gets a hard memory+swap ceiling from its Quadlet
# (Memory=... plus PodmanArgs=--memory-swap=... set equal to Memory=..., i.e.
# containers already can't swap past their own cap). ollama.service had NO
# equivalent: MemoryMax=infinity, MemorySwapMax=infinity. Operator's own
# read on the incident: the kernel silently pulling swap for the model
# process is what let a boot-storm turn into an unresponsive lockup instead
# of a loud, logged, diagnosable event.
#
# This script is the visibility half of the fix (see also the operator-
# applied MemorySwapMax cap on ollama.service itself, which is the hard
# guardrail -- this script alerts the moment Ollama's cgroup touches swap AT
# ALL, well before any hard cap would trip, on a topic dedicated to this one
# question so it's unambiguous on the phone.
#
# No sudo needed: reading a unit's cgroup accounting via `systemctl show`
# works for any user, root or not -- only *writing* MemorySwapMax requires
# root, which is why that half is a drop-in for the operator to apply, not
# this script's job.

set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/ollama-swap-alert"
STATE_FILE="${STATE_DIR}/state"
NTFY_URL="${NTFY_URL:-http://127.0.0.1:2586}"
# Reuses the existing "ops-health" topic (already wired to /status in
# common/ntfy_push.py's TOPIC_CLICK, already something the operator
# monitors for feed health) rather than a brand-new topic the phone
# hasn't subscribed to yet.
NTFY_TOPIC="ops-health"
CHECK_INTERVAL=30
ESCALATE_BYTES=$((1024 * 1024 * 1024))   # 1 GiB -- bump from priority 3 to 4

# Same secrets file every other dispatch component reads. Token may be
# stored as "token:label" -- strip the suffix same as common/ntfy_push.py.
SECRETS_ENV="/etc/corporatetraveldc/dispatch-secrets.env"
NTFY_TOKEN=""
if [[ -r "${SECRETS_ENV}" ]]; then
    NTFY_TOKEN="$(grep -E '^NTFY_TOKEN=' "${SECRETS_ENV}" | head -1 | cut -d= -f2- | cut -d: -f1)"
fi

mkdir -p "${STATE_DIR}"
[[ -f "${STATE_FILE}" ]] || echo "0" > "${STATE_FILE}"

human_mb() {
    # $1 = bytes -> "N MB"
    echo "$(( $1 / 1024 / 1024 )) MB"
}

push() {
    local title="$1" message="$2" priority="$3"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && auth_args=(-H "Authorization: Bearer ${NTFY_TOKEN}")
    curl -s -m 10 -X POST "${NTFY_URL}/${NTFY_TOPIC}" \
        "${auth_args[@]}" \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: floppy_disk" \
        -d "${message}" >/dev/null 2>&1
}

while true; do
    swap_bytes="$(systemctl show ollama --property=MemorySwapCurrent --value 2>/dev/null || echo 0)"
    mem_bytes="$(systemctl show ollama --property=MemoryCurrent --value 2>/dev/null || echo 0)"

    # MemorySwapCurrent/MemoryCurrent read "[not set]" if the unit itself
    # isn't loaded (ollama.service down) -- treat that as 0, not an error.
    [[ "${swap_bytes}" =~ ^[0-9]+$ ]] || swap_bytes=0
    [[ "${mem_bytes}" =~ ^[0-9]+$ ]] || mem_bytes=0

    prev_state="$(cat "${STATE_FILE}" 2>/dev/null || echo 0)"

    if [[ "${swap_bytes}" -gt 0 && "${prev_state}" -eq 0 ]]; then
        push "Ollama swap engaged" \
             "Ollama now using $(human_mb "${swap_bytes}") swap, $(human_mb "${mem_bytes}") resident. Was 0 last check (${CHECK_INTERVAL}s ago)." \
             3
        echo "${swap_bytes}" > "${STATE_FILE}"
    elif [[ "${swap_bytes}" -gt "${ESCALATE_BYTES}" && "${prev_state}" -le "${ESCALATE_BYTES}" ]]; then
        push "Ollama swap climbing" \
             "Ollama swap crossed 1 GiB: $(human_mb "${swap_bytes}") swap, $(human_mb "${mem_bytes}") resident." \
             4
        echo "${swap_bytes}" > "${STATE_FILE}"
    elif [[ "${swap_bytes}" -eq 0 && "${prev_state}" -gt 0 ]]; then
        push "Ollama swap cleared" \
             "Ollama swap back to 0. Was $(human_mb "${prev_state}") last check." \
             2
        echo "0" > "${STATE_FILE}"
    elif [[ "${swap_bytes}" != "${prev_state}" ]]; then
        # Non-crossing change (e.g. 200MB -> 600MB, still under 1GiB) --
        # update state silently, no push. Keeps the topic to real signal.
        echo "${swap_bytes}" > "${STATE_FILE}"
    fi

    sleep "${CHECK_INTERVAL}"
done
