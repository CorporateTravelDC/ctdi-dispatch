#!/bin/bash
# scripts/ollama-wedged-detector.sh
#
# 2026-08-14: built for the timeout-removal observation window (OLLAMA_TIMEOUT
# raised to 3600s, OLLAMA_LOAD_TIMEOUT to 1800s in dispatch.env) -- with wall-
# clock cutoffs loosened way out, this replaces "give up after N seconds
# regardless of progress" with "only flag/act if genuinely making zero forward
# progress." Same technique used live earlier today to confirm a slow-looking
# llama-server was actually still computing (CPU ticks over a short interval,
# via /proc/<pid>/stat), not a formal systemd/cgroup mechanism.
#
# A process is "wedged" here if it is D-state (uninterruptible sleep) or
# R/S-state with essentially zero accumulated CPU ticks across consecutive
# 5s samples. Real generation (even slow, even under contention) always
# shows SOME CPU tick movement each interval; true zero movement sustained
# over time is the actual "nothing is happening" signal a wall-clock
# timeout was a poor proxy for.
#
# 2026-08-15: escalation ladder, all four points derived from the same
# measured number -- the shared dispatcher persona (411 tokens, no
# skill-specific data yet) cost ~53s worst-case under genuine TIER2+
# contention (load 20.94->34.69, unshed). See docs/ Phase 3 of the
# 2026-08-15 model rebuild.
#   T+65s  MONITORING_START_S  -- enter elevated logging/awareness. Below
#          this, a slow-but-real call is unremarkable.
#   T+80s  TIER1_SHED_S = PERSONA_ONLY_GUARD_S (measured 53s x 1.5) -- the
#          absolute floor no per-skill timeout should ever be set below,
#          since even a call doing NOTHING but the persona can legitimately
#          cost this much on a bad day. First real mitigation: shed the
#          SAME tier-1 SWIM feeds thermal-ingest-guard.py sheds (tfms,
#          stdds) via scripts/ingest-feed-ctl.sh, on the theory the wedge
#          may just be resource contention, same class of problem as
#          tonight's TIER2 thermal event.
#   T+110s TIER2_SHED_S -- still zero progress after mitigation attempt
#          #1; shed tier-2 feeds too (fdps, tbfm, itws).
#   T+120s FORCE_KILL_S -- 10s past tier-2 shed and still nothing: both
#          mitigation attempts failed to restore progress, which is no
#          longer explained by ordinary contention -- treat as a possible
#          compromised/broken state. Unlike TIER1/TIER2 shed (fully
#          automated, low blast radius, reversible), the force-kill itself
#          requires human sign-off: routed through
#          scripts/sudo-approval-gate.sh at max ntfy priority (5), 10-min
#          TTL, FAIL-CLOSED on denial/expiry/no-response (operator
#          decision 2026-08-15 -- destroying a possibly-compromised
#          process's state without a human's eyes on it first is itself a
#          risk, and "silence is never consent" is this script's own
#          existing design). Once (and only once) approved, the kill
#          itself is aggressive (SIGKILL, not a graceful restart -- see
#          the intervention block below for why graceful doesn't work
#          here) and the subsequent restart uses the pre-existing
#          unrestricted restart grant, same as this script already did
#          before tonight.
# Any real CPU progress observed at any point resets the whole ladder --
# these thresholds only ever apply to a single sustained zero-progress
# episode, not cumulative time across the process's life.
#
# Usage:
#   ollama-wedged-detector.sh                  # watch + log, one-shot check
#   ollama-wedged-detector.sh --loop            # check every CHECK_INTERVAL_S, forever (or until Ctrl-C)
#   ollama-wedged-detector.sh --kill-if-wedged  # with --loop, run the escalation ladder's real actions (shed/force-kill); without it, ladder is log-only
#
# ASCII output only -- no Unicode symbols (repo convention).

set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FEED_CTL="${REPO_DIR}/scripts/ingest-feed-ctl.sh"
APPROVAL_GATE="${REPO_DIR}/scripts/sudo-approval-gate.sh"
TIER1_FEEDS="tfms,stdds"       # same defaults as thermal-ingest-guard.py
TIER2_FEEDS="fdps,tbfm,itws"

MONITORING_START_S=65
PERSONA_ONLY_GUARD_S=80
TIER1_SHED_S=80
TIER2_SHED_S=110
FORCE_KILL_S=120

CHECK_INTERVAL_S="${WEDGED_CHECK_INTERVAL_S:-10}"
KILL_IF_WEDGED=0
LOOP=0

for arg in "$@"; do
    case "$arg" in
        --loop) LOOP=1 ;;
        --kill-if-wedged) KILL_IF_WEDGED=1 ;;
    esac
done

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# ntfy alert, same pattern as scripts/brief-fallback-monitor.sh -- shares
# its "ops-health" topic so escalation events land alongside the other
# brief-health alerts instead of a separate topic nobody's watching.
NTFY_ENV_FILE=/etc/corporatetraveldc/dispatch.env
NTFY_SECRETS_FILE=/etc/corporatetraveldc/dispatch-secrets.env
_read_env_var() {
    local key="$1" file="$2"
    [ -r "$file" ] || return 0
    sed -nE "s/^${key}=(.*)\$/\1/p" "$file" | tail -1 | sed -E "s/^\"(.*)\"\$/\1/; s/^'(.*)'\$/\1/"
}
NTFY_BASE="$(_read_env_var NTFY_BASE_URL "$NTFY_ENV_FILE")"; NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_TOKEN="$(_read_env_var NTFY_TOKEN "$NTFY_SECRETS_FILE")"; NTFY_TOKEN="${NTFY_TOKEN%%:*}"
WEDGE_ALERT_TOPIC="${WEDGE_ALERT_TOPIC:-ops-health}"
ntfy_alert() {  # $1=title $2=body $3=priority(1-5)
    local auth=()
    [ -n "$NTFY_TOKEN" ] && auth=(-H "Authorization: Bearer ${NTFY_TOKEN}")
    curl -s -m 10 "${auth[@]}" \
        -H "Title: $1" -H "Priority: ${3:-4}" -H "Tags: rotating_light,robot" \
        -d "$2" "${NTFY_BASE}/${WEDGE_ALERT_TOPIC}" >/dev/null 2>&1 || true
}

find_llama_server_pid() {
    pgrep -f "llama-server.*--model" | head -1
}

cpu_ticks() {
    local pid="$1"
    awk '{print $14+$15}' "/proc/${pid}/stat" 2>/dev/null
}

proc_state() {
    local pid="$1"
    awk '{print $3}' "/proc/${pid}/stat" 2>/dev/null
}

check_once() {
    local pid
    pid=$(find_llama_server_pid)
    if [[ -z "$pid" ]]; then
        log "idle -- no llama-server process running"
        echo 0
        return
    fi

    local t1 t2 state
    t1=$(cpu_ticks "$pid")
    state=$(proc_state "$pid")
    if [[ -z "$t1" ]]; then
        log "pid ${pid} disappeared mid-check -- treating as idle"
        echo 0
        return
    fi
    sleep 5
    t2=$(cpu_ticks "$pid")
    if [[ -z "$t2" ]]; then
        log "pid ${pid} exited during 5s sample window -- request likely just completed"
        echo 0
        return
    fi

    local delta=$(( t2 - t1 ))
    if [[ "$delta" -le 0 ]]; then
        log "pid ${pid} state=${state} -- ZERO CPU progress in 5s sample (possible wedge)"
        echo 1
    else
        log "pid ${pid} state=${state} -- ${delta} ticks in 5s (alive, making progress)"
        echo 0
    fi
}

if [[ "$LOOP" -eq 0 ]]; then
    check_once >/dev/null
    exit 0
fi

log "starting wedged-detector loop: check every ${CHECK_INTERVAL_S}s -- ladder at ${MONITORING_START_S}s/${TIER1_SHED_S}s/${TIER2_SHED_S}s/${FORCE_KILL_S}s"

wedge_since=0        # epoch seconds of the first zero-progress sample in this episode, 0 = no active episode
monitoring_logged=0
tier1_shed_done=0
tier2_shed_done=0
force_kill_done=0

reset_episode() {
    wedge_since=0
    monitoring_logged=0
    tier1_shed_done=0
    tier2_shed_done=0
    force_kill_done=0
}

while true; do
    result=$(check_once)
    now=$(date +%s)

    if [[ "$result" == "1" ]]; then
        if [[ "$wedge_since" -eq 0 ]]; then
            wedge_since=$now
            log "zero-progress episode started"
        fi
        elapsed=$(( now - wedge_since ))
        log "zero-progress for ${elapsed}s (ladder: ${MONITORING_START_S}/${TIER1_SHED_S}/${TIER2_SHED_S}/${FORCE_KILL_S})"

        if [[ "$elapsed" -ge "$MONITORING_START_S" && "$monitoring_logged" -eq 0 ]]; then
            log "!! T+${elapsed}s -- entering active monitoring (>= ${MONITORING_START_S}s)"
            monitoring_logged=1
        fi

        if [[ "$elapsed" -ge "$TIER1_SHED_S" && "$tier1_shed_done" -eq 0 ]]; then
            log "!! T+${elapsed}s -- TIER1 shed: stopping ${TIER1_FEEDS} (>= ${TIER1_SHED_S}s persona-only guard point, possible contention)"
            if [[ "$KILL_IF_WEDGED" -eq 1 ]]; then
                "$FEED_CTL" stop "$TIER1_FEEDS"
                ntfy_alert "Ollama wedge -- TIER1 shed" \
                    "Zero CPU progress for ${elapsed}s. Stopped ${TIER1_FEEDS} to relieve possible contention." 4
            fi
            tier1_shed_done=1
        fi

        if [[ "$elapsed" -ge "$TIER2_SHED_S" && "$tier2_shed_done" -eq 0 ]]; then
            log "!! T+${elapsed}s -- TIER2 shed: tier1 mitigation did not restore progress, stopping ${TIER2_FEEDS} too"
            if [[ "$KILL_IF_WEDGED" -eq 1 ]]; then
                "$FEED_CTL" stop "$TIER2_FEEDS"
                ntfy_alert "Ollama wedge -- TIER2 shed" \
                    "Still zero CPU progress at ${elapsed}s after TIER1 shed. Stopped ${TIER2_FEEDS} as well." 5
            fi
            tier2_shed_done=1
        fi

        if [[ "$elapsed" -ge "$FORCE_KILL_S" && "$force_kill_done" -eq 0 ]]; then
            # 2026-08-15: operator directive -- both mitigation tiers
            # already failed to restore progress by this point (tier1 at
            # 80s, tier2 at 110s, still nothing 10s later). That is no
            # longer explained by ordinary resource contention -- treat it
            # as a possible compromised/broken box. UNLIKE tier1/tier2
            # shed (fully automated -- low blast radius, reversible), the
            # kill itself requires human sign-off first: routed through
            # sudo-approval-gate.sh at max ntfy priority, 10-min TTL,
            # fail-closed (deny/expire/no-response == do not run) -- see
            # this file's header comment for the full reasoning. The gate
            # script pushes its own ntfy Allow/Deny alert, so no separate
            # ntfy_alert call here.
            #
            # Once (and only once) approved: aggressive SIGKILL, not a
            # graceful restart -- a plain `systemctl restart` sends
            # SIGTERM and waits through systemd's normal stop-timeout
            # escalation (stop-sigterm -> stop-watchdog -> kill) before it
            # even starts the new instance, exactly what turned
            # ep-advance's real 02:30 EDT hang tonight into several more
            # minutes of nothing. The restart afterward uses the
            # pre-existing unrestricted restart grant (unchanged from
            # before tonight) and only runs if the kill was actually
            # approved and executed -- never restart a process this
            # script never confirmed was actually killed.
            log "!! T+${elapsed}s -- FORCE KILL: two-tier shed did not help, possible compromise -- requesting approval (max priority, 10min TTL, fail-closed)"
            if [[ "$KILL_IF_WEDGED" -eq 1 ]]; then
                # priority 5 is automatic (command contains "kill" -- see
                # sudo-approval-gate.sh's DR/time-sensitive auto-promotion
                # policy), no override needed here.
                if "$APPROVAL_GATE" "wedge-detector-force-kill-ollama" \
                    "Zero CPU progress for ${elapsed}s despite TIER1 (80s) and TIER2 (110s) shed -- past what ordinary contention explains, possible compromised/broken state" \
                    -- sudo /usr/bin/systemctl kill --signal=SIGKILL ollama.service
                then
                    log "!! approved -- SIGKILL executed, restarting ollama.service"
                    sudo -n /usr/bin/systemctl restart ollama.service
                else
                    log "!! NOT approved within TTL (or gate call failed) -- ollama.service left as-is, not restarting an unconfirmed-killed process"
                fi
            fi
            force_kill_done=1
        fi
    else
        if [[ "$wedge_since" -ne 0 ]]; then
            log "real CPU progress resumed after $(( now - wedge_since ))s -- resetting escalation ladder"
        fi
        reset_episode
    fi
    sleep "$CHECK_INTERVAL_S"
done
