#!/bin/bash
# scripts/ctdc-boot-guard.sh -- decide at every boot whether the reboot was commanded (2026-11-18, operator).
# Root runs the INSTALLED copy from two system units:
#   --boot   corporatetraveldc-boot-guard.service, early (before NetworkManager)
#   --alert  corporatetraveldc-boot-guard-alert.service, after network-online
#
# --boot:
#   * a FRESH /var/lib/ctdc-lockdown/reboot-commanded (written by ctdc-reboot.sh less than 30 min before
#     this boot started) is consumed (deleted) and this boot is marked verified
#     (/run/ctdc-boot-guard/verified) -- nothing else happens;
#   * otherwise (missing, stale, unreadable) the reboot is UNCOMMANDED: a persistent hold
#     /var/lib/ctdc-lockdown/uncommanded-reboot-hold is written (boot id, time, last journal line of the
#     previous boot when cheaply known); this boot is NOT marked verified.
# --alert:
#   * if the hold belongs to this boot and has not been alerted: one priority-5 ntfy alert through the
#     same root alert path lockdown.sh uses (publish-only root token file); journal line always;
#   * if the persistent lockdown marker exists: re-asserts the lockdown by running the installed
#     lockdown.sh ("re-asserted at boot"), which re-enters the OOB channel (scripts/ctdc-oob.sh).
# --boot also removes a stale sshd OOB drop-in before sshd starts (it would bind tailnet addresses
# that do not exist yet); the re-assert puts it back.
# The failover polkit rule refuses NetworkManager changes from the operator account while a hold or a
# lockdown marker exists, AND whenever this boot is not marked verified -- so if this guard does not run
# at all, the rule fails closed (NetworkManager's default "auth"), never open.
# ASCII output only.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"
# Test seam (honoured only when NOT root): CTDC_GUARD_TEST_ROOT=<dir> puts every path under <dir>, skips
# the integrity self-check, takes the boot id / boot start from CTDC_GUARD_TEST_BOOT_ID /
# CTDC_GUARD_TEST_BOOT_EPOCH, and never sends ntfy or runs lockdown.sh (it echoes what it would do).
TEST_ROOT=""
if [[ -n "${CTDC_GUARD_TEST_ROOT:-}" && $EUID -ne 0 ]]; then TEST_ROOT="${CTDC_GUARD_TEST_ROOT}"; fi

if [[ -z "$TEST_ROOT" ]]; then
    if [[ -f "${SELF_DIR}/.ctdc-installed" ]]; then
        selfcheck() { "${SELF_DIR}/installed-check.sh" "$@"; }
    else
        selfcheck() { local t; for t in "$@"; do "${REPO_ROOT}/scripts/verify-manifest.sh" "$t" || return 1; done; }
    fi
    if ! selfcheck "scripts/ctdc-boot-guard.sh"; then
        echo "ctdc-boot-guard: INTEGRITY CHECK FAILED -- refusing to run (boot stays unverified: the polkit rule fails closed)" >&2
        exit 1
    fi
    [[ $EUID -eq 0 ]] || { echo "ctdc-boot-guard: must run as root" >&2; exit 77; }
fi

FRESH_S=1800
LOCKDOWN_DIR="${TEST_ROOT}/var/lib/ctdc-lockdown"
COMMANDED="${LOCKDOWN_DIR}/reboot-commanded"
HOLD="${LOCKDOWN_DIR}/uncommanded-reboot-hold"
PERSIST="${LOCKDOWN_DIR}/lockdown-active"
VERIFIED_DIR="${TEST_ROOT}/run/ctdc-boot-guard"
VERIFIED="${VERIFIED_DIR}/verified"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"

say() { echo "[$(date -u '+%H:%M:%SZ')] $*"; }
logit() { say "$*"; [[ -n "$TEST_ROOT" ]] && return 0; logger -t ctdc-boot-guard -- "$*" 2>/dev/null || true; }
boot_id() { if [[ -n "$TEST_ROOT" ]]; then echo "${CTDC_GUARD_TEST_BOOT_ID:-test-boot}"; else cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown; fi; }
boot_epoch() {
    if [[ -n "$TEST_ROOT" ]]; then echo "${CTDC_GUARD_TEST_BOOT_EPOCH:-$(date -u +%s)}"; return; fi
    local up; up="$(cut -d. -f1 /proc/uptime 2>/dev/null || echo 0)"; echo $(( $(date -u +%s) - up ))
}

mode="${1:-}"
case "$mode" in
--boot)
    install -d -m 0755 "$LOCKDOWN_DIR" 2>/dev/null || mkdir -p "$LOCKDOWN_DIR"
    install -d -m 0755 "$VERIFIED_DIR" 2>/dev/null || mkdir -p "$VERIFIED_DIR"
    rm -f "$VERIFIED"
    # 2026-11-18: the sshd OOB drop-in listens on tailnet addresses only; at boot those do not exist yet,
    # so drop it before sshd starts (the --alert re-assert, via lockdown.sh, puts it back).
    if [[ -n "$TEST_ROOT" ]]; then echo "TEST: ctdc-oob.sh boot-reset"
    elif [[ -x "${SELF_DIR}/ctdc-oob.sh" ]]; then "${SELF_DIR}/ctdc-oob.sh" boot-reset || logit "ctdc-oob boot-reset returned non-zero"; fi
    BID="$(boot_id)"; B0="$(boot_epoch)"
    verdict=uncommanded; why="no commanded-reboot record"
    if [[ -f "$COMMANDED" ]]; then
        rec="$(head -c 400 "$COMMANDED" | tr -cd '[:print:] ')"
        ep="$(sed -nE 's/.*epoch=([0-9]+).*/\1/p' <<<"$rec")"
        rm -f "$COMMANDED"                      # consumed either way: a record is good for one boot only
        if [[ -n "$ep" ]] && (( ep <= B0 + 60 )) && (( B0 - ep < FRESH_S )); then
            verdict=commanded; why="${rec}"
        else
            why="stale or unreadable commanded-reboot record (${rec:0:120})"
        fi
    fi
    if [[ "$verdict" == commanded ]]; then
        printf 'boot_id=%s verified=%s UTC commanded: %s\n' "$BID" "$(date -u '+%Y-%m-%d %H:%M:%S')" "${why:0:240}" > "$VERIFIED"
        logit "commanded reboot verified (${why:0:200}); no hold"
    else
        prev=""
        [[ -z "$TEST_ROOT" ]] && prev="$(timeout 5 journalctl -b -1 -n 1 -o short-iso --no-pager 2>/dev/null | tail -n 1 | cut -c1-25 || true)"
        printf 'boot_id=%s held=%s UTC prev_boot_last=%s alerted=no why=%s\n' "$BID" "$(date -u '+%Y-%m-%d %H:%M:%S')" "${prev:-unknown}" "${why:0:200}" > "${HOLD}.new"
        chmod 0644 "${HOLD}.new" && mv -f "${HOLD}.new" "$HOLD"
        logit "UNCOMMANDED reboot (${why}); hold written: ${HOLD} -- failover NetworkManager changes refused until the operator clears it"
    fi
    ;;
--alert)
    BID="$(boot_id)"
    if [[ -f "$HOLD" ]] && grep -q "boot_id=${BID} " "$HOLD" && grep -q 'alerted=no' "$HOLD"; then
        msg="Uncommanded reboot detected (boot ${BID:0:8}). Failover route changes are HELD until you clear it: sudo /usr/local/libexec/ctdc/ctdc-reboot.sh --clear-hold --reason '...'. Reboot properly with ctdc-reboot.sh --reason '...'."
        sent=0
        if [[ -n "$TEST_ROOT" ]]; then
            echo "TEST: ntfy priority 5: ${msg}"; sent=1
        else
            env_get() { [[ -f "${ENV_FILE}" ]] && grep -m1 "^$1=" "${ENV_FILE}" 2>/dev/null | cut -d= -f2- | tr -d '"\r' || true; }
            base="$(env_get NTFY_BASE_URL)"; base="${base:-http://127.0.0.1:2586}"
            topic="$(env_get NTFY_HOT_TOPIC)"; topic="${topic:-hot-alerts}"
            tf="${NTFY_ROOT_TOKEN_FILE:-/etc/corporatetraveldc/ntfy-root.token}"; tok=""
            [[ -r "$tf" ]] && tok="$(head -c 200 "$tf" | tr -d '[:space:]')"
            for i in $(seq 1 20); do               # ntfy is a user container: it may come up after network-online
                fd=""; auth=()
                if [[ -n "$tok" ]]; then exec {fd}<<<"Authorization: Bearer ${tok}"; auth=(-H "@/dev/fd/${fd}"); fi
                if curl -sf --max-time 5 "${auth[@]}" -H "Title: Uncommanded reboot -- failover held" -H "Priority: 5" \
                        -d "$msg" "${base}/${topic}" >/dev/null 2>&1; then sent=1; fi
                [[ -n "$fd" ]] && exec {fd}<&-
                (( sent )) && break
                sleep 30
            done
        fi
        if (( sent )); then sed -i 's/alerted=no/alerted=yes/' "$HOLD"; logit "uncommanded-reboot alert sent"
        else logit "uncommanded-reboot alert could NOT be sent (ntfy unreachable); the hold stands"; fi
    fi
    if [[ -f "$PERSIST" ]]; then
        logit "persistent lockdown marker present -- re-asserting the lockdown"
        if [[ -n "$TEST_ROOT" ]]; then echo "TEST: lockdown.sh --reason re-asserted at boot (persistent lockdown marker)"
        else "${SELF_DIR}/lockdown.sh" --reason "re-asserted at boot (persistent lockdown marker)" || logit "lockdown re-assert returned non-zero"; fi
    fi
    ;;
*) echo "usage: ctdc-boot-guard.sh --boot | --alert" >&2; exit 64 ;;
esac
exit 0
