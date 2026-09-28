#!/usr/bin/env bash
# scripts/check-timer-requires.sh -- permanent guard against a .timer unit
# carrying Requires=/Wants= for its own .service in the [Unit] section.
# See docs/BOOT_STORM_TIMER_REQUIRES.md for the full incident writeup.
#
# Short version: that line is an ordinary dependency, not a schedule --
# starting the TIMER starts the service. So the service fires on every
# boot the instant timers.target is reached (and on any `systemctl
# restart <name>.timer`), regardless of OnCalendar=/Persistent=. Proven
# live 2026-09-05: restarting only corporatetraveldc-thermal-sample.timer
# ran its service immediately, 104s ahead of its own next scheduled fire.
#
# On the 2026-09-05 17:47 reboot this fired 61 timers' services at once.
# 14 were LLM report-tier skills, which pulled in the on-demand
# llama-report-1 tier alongside resident hot+chat -- ~9.8G RSS of 15G,
# hard swap thrash, load1 43-58 -- tripping thermal-ingest-guard LOCKDOWN,
# which shed poller/pusher/runner/ingest and tore down the stack the
# boot-stagger units had just brought up. ~30 min platform outage that
# could not self-recover (resume needs load1 < 15 for 300s).
#
# The timer->service binding is IMPLICIT from the filename ([Timer] Unit=
# defaults to the same basename with .service), so the line was never
# needed for the timer to work. Note that Wants= is NOT a safe substitute
# for Requires= here: it is weaker only for failure propagation and pulls
# the unit in at start identically -- that mistake is exactly why the
# 2026-08-30 llama-restart.timer "fix" left the bug in place.
#
# Checks BOTH the tracked units in this repo AND (when run on the live
# box) the deployed copies under ~/.config/systemd/user -- tracked-file
# discipline alone doesn't stop a later hand-edit of an installed unit.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

FAIL=0

# Print any self-referential Requires=/Wants= found in $1's [Unit] section.
check_timer() {
    local f="$1"
    [[ -f "$f" ]] || return 0
    local base target hits
    base="$(basename "$f" .timer)"
    target="${base}.service"
    hits="$(awk -v t="${target}" '
        /^\[[A-Za-z]+\]/ { in_unit = ($0 == "[Unit]"); next }
        in_unit && ($0 == "Requires=" t || $0 == "Wants=" t) { print FNR": "$0 }
    ' "$f")"
    if [[ -n "${hits}" ]]; then
        echo "[check-timer-requires] FAIL -- self-referential dependency in ${f}:"
        echo "${hits}" | sed 's/^/    /'
        FAIL=1
    fi
}

for f in .config/systemd/user/*.timer; do
    check_timer "${f}"
done

if [[ -d "${HOME}/.config/systemd/user" ]]; then
    for f in "${HOME}"/.config/systemd/user/*.timer; do
        check_timer "${f}"
    done
fi

if [[ "${FAIL}" -eq 1 ]]; then
    echo ""
    echo "[check-timer-requires] A timer's [Unit] Requires=/Wants= on its own service"
    echo "                        makes that service run at every boot, not on schedule."
    echo "                        Delete the line -- the binding is implicit from the"
    echo "                        filename. Do NOT swap Requires= for Wants=."
    echo "                        See docs/BOOT_STORM_TIMER_REQUIRES.md."
    exit 1
fi

echo "[check-timer-requires] OK -- no self-referential timer dependencies found."
