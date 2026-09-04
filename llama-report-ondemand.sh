#!/usr/bin/env bash
# scripts/llama-report-ondemand.sh -- on-demand lifecycle for
# corporatetraveldc-llama-report-1.service (llama.cpp report tier, -c 8192,
# port 8095).
#
# 2026-08-30: report-1 was shelved 2026-08-27 (disabled/inactive) after two
# real near-OOM incidents from running it PERMANENTLY resident alongside
# hot+chat -- but shelving it silently broke every persona whose declared
# num_ctx exceeds chat's -c 4096 (ep-advance 6144, dispatch-desk-memo 8192,
# secondbrain-weekly 6144; see common/personas.py and common/llm.py's
# ollama_post_with_retry() routing comment): llama-chat rejected their real
# briefs with "request (5908 tokens) exceeds the available context size
# (4096 tokens)" for days. The operator-approved fix is ON-DEMAND: report-1
# runs only for the duration of a consumer skill's own run.
#
# Why a host-side script instead of the skill starting it itself: skill code
# runs inside a podman container and structurally cannot reach the host's
# `systemctl --user` (see common/llama_pool.py's module docstring -- this
# exact boundary is what killed the original elastic-pool design). Quadlet
# ExecStartPre/ExecStopPost hooks run ON THE HOST, before/after the
# container, sidestepping that entirely -- same idiom as
# corporatetraveldc-llama-chat.service's own health-blocking ExecStartPre.
#
# Usage (from a consumer skill's quadlet [Service] section):
#   ExecStartPre=/opt/.../scripts/llama-report-ondemand.sh start
#   ExecStopPost=/opt/.../scripts/llama-report-ondemand.sh stop-if-idle %n
#
# start:        systemctl --user start report-1, then block until its
#               /health returns 200 (same bounded-retry shape as
#               llama-chat.service's ExecStartPre). Fails (exit 1) if it
#               never comes healthy -- the consumer container then doesn't
#               start at all, surfacing as a normal unit failure instead of
#               a run that silently falls back.
# stop-if-idle: stop report-1 ONLY if no OTHER consumer unit is currently
#               running. Real overlap exists in the schedules: ep-advance
#               fires hourly at :35 with a 4500s TimeoutStartSec, while
#               dispatch-desk-memo (Sun 09:30, up to ~2.9h budget) and
#               second-brain-weekly (Sun 18:15, up to ~1.5h budget) both
#               span multiple :35 marks -- an unconditional stop from one
#               would kill report-1 out from under the other mid-request.
set -uo pipefail

REPORT_UNIT="corporatetraveldc-llama-report-1.service"
# Same host/port as the unit's own --host/--port launch args and
# common/llama_pool.py's REPORT_PORTS[0].
REPORT_HEALTH_URL="http://100.x.x.x:8095/health"

# Every quadlet-generated consumer unit whose persona declares
# num_ctx > 4096 (common/personas.py) -- keep in lockstep with the quadlets
# that carry the ExecStartPre/ExecStopPost hooks above. If a new persona
# grows past 4096, its quadlet gets the same two hooks AND a row here.
CONSUMERS=(
    corporatetraveldc-ep-advance.service
    corporatetraveldc-dispatch-desk-memo.service
    corporatetraveldc-second-brain-weekly.service
)

case "${1:-}" in
    start)
        # `systemctl start` on an already-active unit is a no-op, so an
        # overlapping second consumer just falls through to the (instant)
        # health check below -- no refcounting needed on the start side.
        if ! systemctl --user start "${REPORT_UNIT}"; then
            echo "llama-report-ondemand: failed to start ${REPORT_UNIT}" >&2
            exit 1
        fi
        for _ in $(seq 1 60); do
            curl -sf "${REPORT_HEALTH_URL}" >/dev/null && exit 0
            sleep 2
        done
        echo "llama-report-ondemand: ${REPORT_UNIT} did not become healthy in time" >&2
        exit 1
        ;;
    stop-if-idle)
        SELF_UNIT="${2:-}"
        for unit in "${CONSUMERS[@]}"; do
            # Skip the caller's own unit -- it's the one currently
            # deactivating (its ExecStopPost is what invoked us).
            [[ "${unit}" == "${SELF_UNIT}" ]] && continue
            state="$(systemctl --user is-active "${unit}" 2>/dev/null || true)"
            # SUBTLETY: every consumer is Type=oneshot without
            # RemainAfterExit, so a consumer MID-RUN reports "activating",
            # never "active" -- a bare `is-active` exit-code check would
            # always see "not active" and defeat the guard entirely.
            case "${state}" in
                active|activating|deactivating)
                    echo "llama-report-ondemand: leaving ${REPORT_UNIT} running -- ${unit} is ${state}"
                    exit 0
                    ;;
            esac
        done
        # Defensive: never fail the calling unit's own teardown over a
        # stop hiccup -- report-1 left running is a resource nit, not a
        # correctness failure, and the next consumer's stop-if-idle (or
        # the operator) reaps it.
        # --no-block is load-bearing (2026-08-30, live incident on this
        # pass's second test run): a blocking stop waits for report-1's
        # whole stop job, and a llama-server that just had a request
        # cancelled with part of its KV in zram took ~85s to die (SIGTERM
        # -> stop-watchdog -> SIGKILL). That blew the CALLING unit's 45s
        # TimeoutStopSec while in state 'stop-post' -- systemd SIGABRT'd
        # this very script and marked the consumer unit failed even
        # though the skill run itself had finished cleanly. Fire the stop
        # job asynchronously instead; report-1's own TimeoutStopSec
        # bounds how long the teardown can drag on.
        systemctl --user stop --no-block "${REPORT_UNIT}" \
            || echo "llama-report-ondemand: stop of ${REPORT_UNIT} failed (non-fatal)" >&2
        exit 0
        ;;
    *)
        echo "usage: $0 {start|stop-if-idle <self-unit-name>}" >&2
        exit 2
        ;;
esac
