#!/bin/bash
# scripts/ctdc-reboot.sh -- the sanctioned way to reboot the box (2026-11-18, operator).
# Root runs the INSTALLED copy: sudo /usr/local/libexec/ctdc/ctdc-reboot.sh ...
#
#   sudo ctdc-reboot.sh --reason "TEXT" [--dry-run]   commanded reboot: record it, then systemctl reboot
#   sudo ctdc-reboot.sh --clear-hold [--reason TEXT]  clear ONLY the uncommanded-reboot hold
#   ctdc-reboot.sh --status                           show the guard markers (no root needed)
#
# A reboot made any other way (power loss, crash, a plain `sudo reboot`, someone else's reboot) is
# UNCOMMANDED: at boot, ctdc-boot-guard.sh finds no fresh record here, writes the uncommanded-reboot
# hold, alerts once, and the failover polkit rule (config/polkit/49-ctdc-net-failover.rules) refuses
# NetworkManager changes from the operator account until the operator clears the hold (this script's
# --clear-hold, or restore-network.sh, which clears every lockdown marker).
# ASCII output only.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"
# Test seam (honoured only when NOT root): CTDC_GUARD_TEST_ROOT=<dir> puts every path under <dir>,
# skips the integrity self-check and the root check, and replaces systemctl with an echo.
TEST_ROOT=""
if [[ -n "${CTDC_GUARD_TEST_ROOT:-}" && $EUID -ne 0 ]]; then TEST_ROOT="${CTDC_GUARD_TEST_ROOT}"; fi

if [[ -z "$TEST_ROOT" ]]; then
    if [[ -f "${SELF_DIR}/.ctdc-installed" ]]; then
        selfcheck() { "${SELF_DIR}/installed-check.sh" "$@"; }
    else
        selfcheck() { local t; for t in "$@"; do "${REPO_ROOT}/scripts/verify-manifest.sh" "$t" || return 1; done; }
    fi
    if ! selfcheck "scripts/ctdc-reboot.sh"; then
        echo "ctdc-reboot: INTEGRITY CHECK FAILED -- refusing to run" >&2
        exit 1
    fi
fi

LOCKDOWN_DIR="${TEST_ROOT}/var/lib/ctdc-lockdown"
COMMANDED="${LOCKDOWN_DIR}/reboot-commanded"
HOLD="${LOCKDOWN_DIR}/uncommanded-reboot-hold"
PERSIST="${LOCKDOWN_DIR}/lockdown-active"
RUN_LOCK="${TEST_ROOT}/run/corporatetraveldc-lockdown-active"
VERIFIED_DIR="${TEST_ROOT}/run/ctdc-boot-guard"
VERIFIED="${VERIFIED_DIR}/verified"

say() { echo "[$(date -u '+%H:%M:%SZ')] $*"; }
logit() { [[ -n "$TEST_ROOT" ]] && return 0; logger -t ctdc-reboot -- "$*" 2>/dev/null || true; }
reboot_cmd() { if [[ -n "$TEST_ROOT" ]]; then echo "TEST: systemctl reboot"; else systemctl reboot; fi; }

MODE=""; REASON=""; DRY=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --reason) REASON="${2:-}"; shift 2 ;;
        --clear-hold) MODE=clear; shift ;;
        --status) MODE=status; shift ;;
        --dry-run) DRY=1; shift ;;
        *) echo "usage: ctdc-reboot.sh --reason TEXT [--dry-run] | --clear-hold [--reason TEXT] | --status" >&2; exit 64 ;;
    esac
done
[[ -n "$MODE" ]] || MODE=reboot

if [[ "$MODE" == status ]]; then
    for f in "$RUN_LOCK" "$PERSIST" "$HOLD" "$COMMANDED" "$VERIFIED"; do
        if [[ -e "$f" ]]; then echo "present  ${f}: $(head -c 160 "$f" 2>/dev/null | tr -cd '[:print:] ')"; else echo "absent   ${f}"; fi
    done
    exit 0
fi

if [[ -z "$TEST_ROOT" && $EUID -ne 0 ]]; then echo "ctdc-reboot: run with sudo" >&2; exit 77; fi
BY="${SUDO_USER:-$(id -un)}"

if [[ "$MODE" == clear ]]; then
    if [[ ! -e "$HOLD" ]]; then say "no uncommanded-reboot hold present -- nothing to clear"; exit 0; fi
    say "clearing the uncommanded-reboot hold: $(head -c 160 "$HOLD" | tr -cd '[:print:] ')"
    if (( DRY )); then say "[DRY-RUN] rm -f ${HOLD}; mark this boot verified"; exit 0; fi
    rm -f "$HOLD"
    install -d -m 0755 "$VERIFIED_DIR" 2>/dev/null || mkdir -p "$VERIFIED_DIR"
    printf 'cleared %s UTC by=%s reason=%s\n' "$(date -u '+%Y-%m-%d %H:%M:%S')" "$BY" "${REASON:-hold cleared}" > "$VERIFIED"
    logit "uncommanded-reboot hold cleared by ${BY}: ${REASON:-no reason given}"
    [[ -e "$PERSIST" || -e "$RUN_LOCK" ]] && say "NOTE: a LOCKDOWN marker is still present; only restore-network.sh lifts a lockdown"
    say "hold cleared; the failover watchdog may change routes again"
    exit 0
fi

# commanded reboot
[[ -n "$REASON" ]] || { echo "ctdc-reboot: --reason TEXT is required (it goes in the record)" >&2; exit 64; }
install -d -m 0755 "$LOCKDOWN_DIR" 2>/dev/null || mkdir -p "$LOCKDOWN_DIR"
REC="epoch=$(date -u +%s) utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ') by=${BY} reason=${REASON:0:200}"
if (( DRY )); then say "[DRY-RUN] would write ${COMMANDED}: ${REC}"; say "[DRY-RUN] would run: systemctl reboot"; exit 0; fi
printf '%s\n' "$REC" > "${COMMANDED}.new" && chmod 0644 "${COMMANDED}.new" && mv -f "${COMMANDED}.new" "$COMMANDED"
# flush only the record and its directory: a box-wide sync can stall for minutes under load
# (2026-11-18: it blew the test's 30 s timeout at load 29); systemctl reboot flushes the rest itself
[[ -n "$TEST_ROOT" ]] || sync -- "$COMMANDED" "$LOCKDOWN_DIR" 2>/dev/null || true
logit "commanded reboot by ${BY}: ${REASON}"
say "commanded reboot recorded (${COMMANDED}); rebooting now"
reboot_cmd
