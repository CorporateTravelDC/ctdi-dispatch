#!/bin/bash
# scripts/gui-window.sh -- on-demand graphical maintenance window.
#
# Manual, operator-invoked spin-up / teardown of a self-contained headless
# desktop session on this (monitor-less, production) box, for the short
# jobs that genuinely need a GUI: pairing a new agent's desktop app,
# a vendor tool with no CLI, a one-time visual check. The lockdown /
# maintenance-window pattern applied to a display: engage, do the work,
# disengage, and leave NOTHING behind.
#
# Why this shape (2026-10-03): the first such bootstrap (Claude desktop,
# 2026-08) left a GDM autologin + an XDG autostart entry that
# systemd-xdg-autostart-generator kept resurrecting on every boot until it
# was found weeks later. This script exists so the next one can't.
#
# Everything is USER-level and runtime-only, same philosophy as
# maintenance-window-on.sh (--runtime) and lockdown.sh ("narrow, fast,
# reversible"):
#   - Xvfb on its own display (no GDM, no seat, no Xorg on real hardware)
#   - i3 as the window manager (already installed, 1 process, ~17 MB) with a
#     repo-owned config (config/gui-window-i3.config) -- NOT /etc/i3/config,
#     whose dex-autostart line would re-launch every ~/.config/autostart
#     entry the previous pairing session left behind
#   - x11vnc bound to LOOPBACK ONLY; reached over Tailscale via an SSH tunnel.
#     Nothing new listens on any routable interface, no firewalld change.
#   - a transient systemd-run timer tears the whole thing down after
#     --duration (default 4h) even if the operator forgets -- the window is
#     bounded by construction, like a fail2ban ban is.
#   - llama CPUWeight suppressed for the window (maintenance-window-on.sh)
#     and restored on teardown (maintenance-window-off.sh), so the session
#     actually gets scheduled.
#   - state lives in /run/user/<uid> (tmpfs): a reboot is also a teardown.
#   - teardown SWEEPS for leftovers (autostart entries, new user units,
#     generator output) and runs the CLAUDE.md drift check, reporting rather
#     than deleting -- a leftover is an operator decision, but it is never
#     silent.
#
# The VNC password is a credential: the OPERATOR creates it, once,
# interactively (`x11vnc -storepasswd ~/.secrets/gui-window.vncpass`).
# This script never generates, prints, or accepts one on argv.
#
# Usage:
#   gui-window.sh on  [--duration 4h] [--geometry 1600x900] [--display :10] [--port 5900]
#   gui-window.sh off [--auto]
#   gui-window.sh status
#   gui-window.sh on --dry-run
#
# Connect (from the phone/laptop, over Tailscale):
#   ssh -L 5900:127.0.0.1:5900 corporatetraveldc@100.x.x.x
#   then point a VNC client at localhost:5900
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail
# 2026-10-05 (argv-token sweep): a bearer token never goes on a command line --
# /proc/<pid>/cmdline is world-readable here (no hidepid), i.e. readable by
# every team account. authhdr NAME TOKEN puts the header on a private fd and
# sets NAME=(-H @/dev/fd/N) for ONE curl call (re-run it before each call).
authhdr() { local -n _ah="$1"; [[ -n "${_AUTHHDR_FD:-}" ]] && exec {_AUTHHDR_FD}<&-; exec {_AUTHHDR_FD}<<<"Authorization: Bearer $2"; _ah=(-H "@/dev/fd/${_AUTHHDR_FD}"); }

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"
if ! "${REPO_ROOT}/scripts/verify-manifest.sh" "scripts/gui-window.sh"; then
    echo "gui-window: INTEGRITY CHECK FAILED -- refusing to run" >&2
    exit 1
fi

STATE_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/corporatetraveldc-gui-window"
STATE_FILE="${STATE_DIR}/state.json"
LOG_FILE="${STATE_DIR}/gui-window.log"
VNC_PASS_FILE="${HOME}/.secrets/gui-window.vncpass"
# NOT /etc/i3/config: the stock one execs dex-autostart (runs every
# ~/.config/autostart entry -- the exact leftover-relaunch failure mode this
# window exists to prevent), i3-config-wizard (writes into $HOME), and an
# idle screen-locker. The repo copy is the default minus those; see its header.
I3_CONFIG="${REPO_ROOT}/config/gui-window-i3.config"
AUTOOFF_UNIT="corporatetraveldc-gui-window-autooff"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"
SECRETS_FILE="/etc/corporatetraveldc/dispatch-secrets.env"
TAILSCALE_IP="100.x.x.x"

DISPLAY_NUM=":10"
GEOMETRY="1600x900"
DURATION="4h"
VNC_PORT="5900"
DRY_RUN=0
AUTO=0

ACTION="${1:-status}"; shift || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --duration) DURATION="${2:-4h}"; shift 2 ;;
        --geometry) GEOMETRY="${2:-1600x900}"; shift 2 ;;
        --display)  DISPLAY_NUM="${2:-:10}"; shift 2 ;;
        --port)     VNC_PORT="${2:-5900}"; shift 2 ;;
        --dry-run)  DRY_RUN=1; shift ;;
        --auto)     AUTO=1; shift ;;
        *)          echo "gui-window: unknown argument: $1" >&2; exit 2 ;;
    esac
done

mkdir -p "${STATE_DIR}"

read_env_var() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    grep -m1 "^${key}=" "${file}" 2>/dev/null | cut -d'=' -f2-
}
NTFY_BASE="$(read_env_var NTFY_BASE_URL "${ENV_FILE}")";  NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_OPS="$(read_env_var NTFY_OPS_TOPIC "${ENV_FILE}")";   NTFY_OPS="${NTFY_OPS:-ops-health}"
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "${SECRETS_FILE}")"; NTFY_TOKEN="${NTFY_TOKEN%%:*}"

say() {
    local line="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "${line}"; echo "${line}" >> "${LOG_FILE}" 2>/dev/null
}
run() { if (( DRY_RUN )); then say "  [DRY-RUN] $*"; else "$@"; fi; }

ntfy_send() {
    local title="$1" msg="$2" priority="${3:-3}"
    local auth_args=()
    [[ -n "${NTFY_TOKEN}" ]] && authhdr auth_args "${NTFY_TOKEN}"
    curl -sf --max-time 5 "${auth_args[@]}" \
        -H "Title: ${title}" -H "Priority: ${priority}" -H "Tags: desktop_computer" \
        -d "${msg}" "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1 \
        || say "warn: ntfy_send failed (token_set=$([[ -n "${NTFY_TOKEN}" ]] && echo yes || echo no))"
}

state_get() { grep -o "\"$1\":\"[^\"]*\"" "${STATE_FILE}" 2>/dev/null | cut -d'"' -f4; }
pid_alive() { [[ -n "${1:-}" ]] && kill -0 "$1" 2>/dev/null; }

write_state() {
    printf '{"display":"%s","geometry":"%s","vnc_port":"%s","duration":"%s","started_epoch":"%s","pid_xvfb":"%s","pid_i3":"%s","pid_vnc":"%s"}\n' \
        "${DISPLAY_NUM}" "${GEOMETRY}" "${VNC_PORT}" "${DURATION}" "$(date +%s)" "$1" "$2" "$3" > "${STATE_FILE}"
}

window_is_on() {
    [[ -f "${STATE_FILE}" ]] || return 1
    pid_alive "$(state_get pid_xvfb)"
}

# ---------------------------------------------------------------- status --
if [[ "${ACTION}" == "status" ]]; then
    if window_is_on; then
        started=$(state_get started_epoch); now=$(date +%s)
        echo "gui-window: ON  (display $(state_get display), ${GEOMETRY} , vnc 127.0.0.1:$(state_get vnc_port))"
        echo "  open for $(( (now - started) / 60 )) min of $(state_get duration); auto-off timer: $(systemctl --user is-active "${AUTOOFF_UNIT}.timer" 2>/dev/null || echo none)"
        for k in pid_xvfb pid_i3 pid_vnc; do printf "  %-8s %s\n" "${k#pid_}" "$(pid_alive "$(state_get "$k")" && echo "alive ($(state_get "$k"))" || echo "DEAD")"; done
        echo "  connect: ssh -L ${VNC_PORT}:127.0.0.1:$(state_get vnc_port) corporatetraveldc@${TAILSCALE_IP}  ->  vnc://localhost:${VNC_PORT}"
    else
        echo "gui-window: OFF"
        [[ -f "${STATE_FILE}" ]] && echo "  (stale state file present -- run 'off' to clean up)"
    fi
    exit 0
fi

# -------------------------------------------------------------------- on --
if [[ "${ACTION}" == "on" ]]; then
    if window_is_on; then
        say "already ON (display $(state_get display)) -- run 'status' or 'off' first"; exit 0
    fi
    for b in Xvfb i3 x11vnc; do
        command -v "$b" >/dev/null 2>&1 || { say "ERROR: $b not installed"; exit 1; }
    done
    [[ -r "${I3_CONFIG}" ]] || { say "ERROR: i3 config missing at ${I3_CONFIG}"; exit 1; }
    if [[ ! -r "${VNC_PASS_FILE}" ]]; then
        say "ERROR: no VNC password file at ${VNC_PASS_FILE}"
        say "  The operator creates it once, interactively (never this script):"
        say "    x11vnc -storepasswd ${VNC_PASS_FILE} && chmod 600 ${VNC_PASS_FILE}"
        exit 2
    fi
    if [[ "$(stat -c %a "${VNC_PASS_FILE}")" != "600" ]]; then
        say "ERROR: ${VNC_PASS_FILE} must be mode 600 (is $(stat -c %a "${VNC_PASS_FILE}"))"; exit 2
    fi
    if [[ -e "/tmp/.X11-unix/X${DISPLAY_NUM#:}" ]]; then
        say "ERROR: display ${DISPLAY_NUM} already has a socket -- pick another with --display"; exit 1
    fi

    say "-------- GUI maintenance window: ON --------"
    say "display ${DISPLAY_NUM}  geometry ${GEOMETRY}  vnc 127.0.0.1:${VNC_PORT}  auto-off ${DURATION}"
    (( DRY_RUN )) && say "[DRY-RUN -- nothing will be started]"

    run "${SELF_DIR}/maintenance-window-on.sh"

    if (( DRY_RUN )); then
        say "  [DRY-RUN] Xvfb ${DISPLAY_NUM} -screen 0 ${GEOMETRY}x24 -nolisten tcp"
        say "  [DRY-RUN] i3 on ${DISPLAY_NUM}"
        say "  [DRY-RUN] x11vnc -display ${DISPLAY_NUM} -localhost -rfbport ${VNC_PORT} -rfbauth <file> -forever -shared"
        say "  [DRY-RUN] systemd-run --user --on-active=${DURATION} --unit=${AUTOOFF_UNIT} $0 off --auto"
        exit 0
    fi

    setsid Xvfb "${DISPLAY_NUM}" -screen 0 "${GEOMETRY}x24" -nolisten tcp >>"${LOG_FILE}" 2>&1 &
    pid_xvfb=$!
    for _ in $(seq 1 25); do [[ -e "/tmp/.X11-unix/X${DISPLAY_NUM#:}" ]] && break; sleep 0.2; done
    if ! pid_alive "${pid_xvfb}" || [[ ! -e "/tmp/.X11-unix/X${DISPLAY_NUM#:}" ]]; then
        say "ERROR: Xvfb did not come up (see ${LOG_FILE})"; "${SELF_DIR}/maintenance-window-off.sh"; exit 1
    fi
    DISPLAY="${DISPLAY_NUM}" setsid i3 -c "${I3_CONFIG}" >>"${LOG_FILE}" 2>&1 &
    pid_i3=$!
    sleep 1
    setsid x11vnc -display "${DISPLAY_NUM}" -localhost -rfbport "${VNC_PORT}" -rfbauth "${VNC_PASS_FILE}" \
        -forever -shared -quiet -noxdamage >>"${LOG_FILE}" 2>&1 &
    pid_vnc=$!
    sleep 1
    for p in "${pid_i3}:i3" "${pid_vnc}:x11vnc"; do
        pid_alive "${p%%:*}" || { say "ERROR: ${p##*:} did not stay up (see ${LOG_FILE})"; kill "${pid_xvfb}" "${pid_i3}" "${pid_vnc}" 2>/dev/null; "${SELF_DIR}/maintenance-window-off.sh"; exit 1; }
    done

    write_state "${pid_xvfb}" "${pid_i3}" "${pid_vnc}"
    systemctl --user stop "${AUTOOFF_UNIT}.timer" "${AUTOOFF_UNIT}.service" >/dev/null 2>&1; systemctl --user reset-failed "${AUTOOFF_UNIT}.service" >/dev/null 2>&1
    if systemd-run --user --quiet --on-active="${DURATION}" --unit="${AUTOOFF_UNIT}" \
        --description="Auto-teardown of the GUI maintenance window after ${DURATION}" \
        "${SELF_DIR}/gui-window.sh" off --auto; then
        say "auto-off scheduled in ${DURATION} (${AUTOOFF_UNIT}.timer)"
    else
        say "warn: could not schedule auto-off -- remember to run 'off' yourself"
    fi

    say "ON. Connect from your device (Tailscale):"
    say "  ssh -L ${VNC_PORT}:127.0.0.1:${VNC_PORT} corporatetraveldc@${TAILSCALE_IP}"
    say "  then VNC -> localhost:${VNC_PORT}   (teardown: $(basename "$0") off)"
    ntfy_send "GUI maintenance window: OPEN" \
        "Headless Xvfb+i3 session on ${DISPLAY_NUM}, VNC on loopback :${VNC_PORT} (ssh -L over Tailscale). llama CPUWeight suppressed. Auto-off in ${DURATION}." 3
    exit 0
fi

# ------------------------------------------------------------------- off --
if [[ "${ACTION}" == "off" ]]; then
    say "-------- GUI maintenance window: OFF$( (( AUTO )) && echo ' (auto-off timer)' ) --------"
    started=$(state_get started_epoch); open_min=0
    [[ -n "${started}" ]] && open_min=$(( ($(date +%s) - started) / 60 ))
    disp="$(state_get display)"; disp="${disp:-${DISPLAY_NUM}}"

    for k in pid_vnc pid_i3 pid_xvfb; do
        p="$(state_get "$k")"
        if pid_alive "${p}"; then run kill -TERM "${p}" 2>/dev/null; fi
    done
    sleep 2
    for k in pid_vnc pid_i3 pid_xvfb; do
        p="$(state_get "$k")"
        if pid_alive "${p}"; then run kill -KILL "${p}" 2>/dev/null; say "  ${k#pid_}: needed SIGKILL"; fi
    done
    # belt-and-suspenders: anything we started on that display, by name.
    # Anchored (^) so the pattern can never match a shell whose own argv
    # happens to contain it -- an unanchored pkill -f killed the very shell
    # running it during this script's smoke test.
    (( DRY_RUN )) || pkill -f "^x11vnc -display ${disp}" 2>/dev/null
    (( DRY_RUN )) || pkill -f "^Xvfb ${disp} " 2>/dev/null
    (( DRY_RUN )) || rm -f "/tmp/.X${disp#:}-lock" "/tmp/.X11-unix/X${disp#:}"

    if (( ! AUTO )); then
        run systemctl --user stop "${AUTOOFF_UNIT}.timer" "${AUTOOFF_UNIT}.service" 2>/dev/null
    fi
    (( DRY_RUN )) || systemctl --user reset-failed "${AUTOOFF_UNIT}.service" >/dev/null 2>&1

    run "${SELF_DIR}/maintenance-window-off.sh"

    # ---- leftover sweep: report, never delete -----------------------------
    say "sweeping for leftovers..."
    leftovers=0
    while IFS= read -r f; do
        say "  LEFTOVER autostart entry (not Hidden=true): ${f}"; leftovers=$((leftovers+1))
    done < <(grep -L "^Hidden=true" "${HOME}"/.config/autostart/*.desktop 2>/dev/null)
    if [[ -f "${STATE_FILE}" ]]; then
        while IFS= read -r f; do
            say "  LEFTOVER new/changed unit since window opened: ${f}"; leftovers=$((leftovers+1))
        done < <(find "${HOME}/.config/systemd/user" "${HOME}/.config/containers/systemd" -maxdepth 1 -type f -newer "${STATE_FILE}" 2>/dev/null)
    fi
    while IFS= read -r u; do
        say "  LEFTOVER XDG-autostart generated unit: ${u}"; leftovers=$((leftovers+1))
    done < <(ls "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/systemd/generator.late/" 2>/dev/null | grep '^app-.*@autostart\.service$')
    if systemctl --user is-active --quiet xdg-desktop-autostart.target 2>/dev/null; then
        say "  LEFTOVER xdg-desktop-autostart.target is ACTIVE"; leftovers=$((leftovers+1))
    fi
    if [[ -x "${REPO_ROOT}/scripts/check-claude-md-drift.sh" ]]; then
        drift_out="$("${REPO_ROOT}/scripts/check-claude-md-drift.sh" 2>&1 | grep -E '^\[(DRIFT|FAIL|WARN)\]' || true)"
        [[ -n "${drift_out}" ]] && { say "  drift check:"; echo "${drift_out}" | sed 's/^/    /' | tee -a "${LOG_FILE}"; }
    fi
    say "leftovers flagged: ${leftovers} (reported only -- nothing deleted; the generator-made app-*@autostart units are pre-existing GNOME ones unless they are new)"

    (( DRY_RUN )) || rm -f "${STATE_FILE}"
    say "OFF after ${open_min} min."
    ntfy_send "GUI maintenance window: CLOSED" \
        "Session on ${disp} torn down after ${open_min} min$( (( AUTO )) && echo ' (auto-off)' ). llama CPUWeight restored. Leftovers flagged: ${leftovers} (see ${LOG_FILE})." $(( leftovers > 0 ? 4 : 2 ))
    exit 0
fi

echo "gui-window: unknown action '${ACTION}' (on|off|status)" >&2
exit 2
