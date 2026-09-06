#!/bin/bash
# scripts/weekly-external-image-update.sh
# Weekly forced update-check for the external/third-party containers (the
# ~11 running an upstream ghcr.io/docker.io image on
# io.containers.autoupdate=registry, as distinct from our own
# localhost/corporatetraveldc-* images, which are only ever refreshed by an
# explicit build-images.sh rebuild after a code change -- see the
# 2026-09-03 standing rule (operator directive): our own containers get
# rebuilt after every major code change; the external ones get checked/
# updated on this weekly cadence instead, since we don't control when
# upstream cuts a release.
#
# Just runs `podman auto-update` -- no per-unit filtering needed. Our own
# local-policy containers are structurally no-ops here regardless (they
# only change when a local rebuild already produced a new image digest,
# which this script never does), so a plain `podman auto-update` only ever
# actually touches the external/registry-policy set in practice.
#
# 2026-09-03 real incident this script's alerting is designed around: a
# manual `podman auto-update` run the same night triggered an UNPLANNED
# Nextcloud MAJOR VERSION jump (33.0.6.x -> 34.0.3.x -- version spelled
# with .x here deliberately: the exact four-part strings look like IPv4
# addresses to scrub-public-tree.py's verify pass, ~2hr migration under
# load) because docker.io/library/nextcloud:stable-apache is a rolling tag,
# not a routine patch bump like the SDR-tool images. It completed cleanly
# that time, but nobody should find out about a stateful/business-critical
# service's version jump after the fact from a cold read of the logs --
# this script always sends an ntfy summary of what actually updated, and
# calls out nextcloud-app/nextcloud-db BY NAME with a higher priority if
# either is in that list, specifically so a major migration on the vault's
# backing store is never silent.
#
# Usage: weekly-external-image-update.sh (no args; called by the timer)
set -uo pipefail

STATE_DIR="/var/lib/corporatetraveldc/weekly-external-image-update"
LOG_FILE="${STATE_DIR}/update.log"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"

mkdir -p "${STATE_DIR}"

# dispatch.env/dispatch-secrets.env are podman --env-file (plain
# KEY=VALUE), not bash-safe to `source` -- same reasoning as
# scheduled-ingest-restart.sh / nextcloud-health-check.sh.
read_env_var() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    grep -m1 "^${key}=" "${file}" 2>/dev/null | cut -d'=' -f2-
}

NTFY_BASE="$(read_env_var NTFY_BASE_URL "${ENV_FILE}")"
NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_OPS="$(read_env_var NTFY_OPS_TOPIC "${ENV_FILE}")"
NTFY_OPS="${NTFY_OPS:-ops-health}"
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "${SECRETS_FILE}")"

log() {
    local level="$1"; shift
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${ts}] [${level^^}] $*" >> "${LOG_FILE}" 2>/dev/null
    echo "[${ts}] [${level^^}] $*"
}

ntfy_send() {
    local title="$1" msg="$2" priority="${3:-2}"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && auth_args=(-H "Authorization: Bearer ${NTFY_TOKEN}")
    curl -sf --max-time 5 \
        "${auth_args[@]}" \
        -H "Title: ${title}" \
        -H "Priority: ${priority}" \
        -H "Tags: package" \
        -d "${msg}" \
        "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1 || log "warn" "ntfy_send failed (token_set=$([[ -n \"${NTFY_TOKEN}\" ]] && echo yes || echo no))"
}

log "info" "weekly external-image update check starting"

out=$(podman auto-update 2>&1)
rc=$?
echo "${out}" >> "${LOG_FILE}"

if [[ ${rc} -ne 0 ]]; then
    log "error" "podman auto-update exited ${rc}"
    ntfy_send "Weekly image update FAILED" \
        "podman auto-update exited ${rc} -- see ${LOG_FILE}" 4
    exit 1
fi

# The table's last column is "true"/"false" for whether that unit's image
# actually changed this run. Match container names (2nd field's
# parenthesized form), not unit names, so this doesn't depend on a stable
# column count if podman's table format ever changes.
updated=$(echo "${out}" | grep -E '\btrue\s*$' | grep -oE '\([a-zA-Z0-9_.-]+\)' | tr -d '()')

if [[ -z "${updated}" ]]; then
    log "info" "no external images updated this run"
    exit 0
fi

count=$(echo "${updated}" | wc -l)
list=$(echo "${updated}" | tr '\n' ',' | sed 's/,$//' | sed 's/,/, /g')
log "info" "updated ${count} container(s): ${list}"

priority=2
title="Weekly image update: ${count} container(s) updated"
if echo "${updated}" | grep -qE '^nextcloud-(app|db)$'; then
    priority=4
    title="Weekly image update: nextcloud updated (check for a version jump)"
fi

ntfy_send "${title}" "Updated: ${list}. Review journalctl --user for each if anything looks off; nextcloud specifically can jump a MAJOR version on a rolling tag, not just a patch." "${priority}"
