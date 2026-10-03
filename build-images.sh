#!/usr/bin/env bash
# build-images.sh — builds all five Corporate Travel DC Dispatch container images
# Run as corporatetraveldc Safe to re-run (rebuilds from cache where possible).
# After running, reload Quadlets: systemctl --user daemon-reload

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DATE="$(date -u +%Y%m%dT%H%M%SZ)"

log()  { echo "[build-images] $*"; }
die()  { echo "[build-images] ERROR: $*" >&2; exit 1; }

# Preflight
[[ "$(id -un)" == "corporatetraveldc" ]] || die "Run as corporatetraveldc, not root"
command -v podman &>/dev/null || die "podman not found"

cd "${SCRIPT_DIR}"

[[ -f requirements.txt ]] || die "requirements.txt not found — run from corporatetraveldc/ root"
[[ -d src/ ]]             || die "src/ not found — run from corporatetraveldc/ root"

log "Building Corporate Travel DC Dispatch container images..."
log "Build context: ${SCRIPT_DIR}"
log "Build date: ${BUILD_DATE}"
log ""

# 2026-09-01: maintenance window -- the llama unit runs at CPUWeight=9000
# (2026-09-06: single corporatetraveldc-llama.service replaced llama-chat)
# (see scripts/maintenance-window-on.sh for why), which starved this
# build of CPU during real-world runs and surfaced as pip read-timeouts.
# Engage suppression for the duration of this build; the trap guarantees
# it's released on any exit path (success, die(), or Ctrl-C).
if command -v systemctl &>/dev/null && systemctl --user is-active --quiet corporatetraveldc-llama.service 2>/dev/null; then
    "${SCRIPT_DIR}/scripts/maintenance-window-on.sh"
    trap '"${SCRIPT_DIR}/scripts/maintenance-window-off.sh"' EXIT
fi

# 2026-10-03 (operator directive): keep a ready-to-go rollback. Before each
# build, re-point :previous at the outgoing :latest. A tagged image is never
# dangling, so scheduled-podman-prune.sh can't reclaim it no matter how old;
# the build before THAT one loses its tag here and becomes prunable once it
# is >24h old. Rollback = `podman tag <svc>:previous <svc>:latest` + restart.
keep_previous() {
    local tag="$1"
    podman image exists "${tag}" 2>/dev/null || return 0
    podman tag "${tag}" "${tag%:latest}:previous" 2>/dev/null \
        || log "  WARNING: could not tag ${tag} as :previous (rollback for this one is by image ID only)"
}

for service in web poller pusher ingest amtrak-tracker; do
    cf="Containerfile.${service}"
    tag="localhost/corporatetraveldc-${service}:latest"
    [[ -f "${cf}" ]] || die "${cf} not found"

    keep_previous "${tag}"
    log "Building ${tag}..."
    podman build \
        -f "${cf}" \
        -t "${tag}" \
        --label "build-date=${BUILD_DATE}" \
        --label "service=${service}" \
        .
    log "  ${tag}: OK"
    log ""
done

log "All five core images built successfully."
# -- dispatch-runner (multi-stage: Node 20 frontend + Python 3.13 backend) ---
keep_previous "localhost/corporatetraveldc-runner:latest"
log "Building localhost/corporatetraveldc-runner:latest..."
if podman build \
    -f Containerfile.runner \
    -t localhost/corporatetraveldc-runner:latest \
    .; then
    log "  localhost/corporatetraveldc-runner:latest: OK"
else
    log "  localhost/corporatetraveldc-runner:latest: FAILED"
    exit 1
fi
log ""
log "All images built successfully (including runner)."
log "Previous builds kept as :previous for rollback. To roll one back:"
log "  podman tag localhost/corporatetraveldc-<svc>:previous localhost/corporatetraveldc-<svc>:latest"
log "  systemctl --user restart corporatetraveldc-<svc>"
log "(Dangling layers older than 24h are reclaimed daily by corporatetraveldc-podman-prune.timer;"
log " :previous is tagged, so it is never dangling and never pruned.)"

log ""
log "Next steps:"
log "  1. systemctl --user daemon-reload"
log "  2. systemctl --user start corporatetraveldc-poller"
log "  3. systemctl --user start corporatetraveldc-web"
log "  4. systemctl --user start corporatetraveldc-pusher"
log "  5. curl http://localhost:8000/healthz"
log "  6. PYTHONPATH=src python3 src/ctdc_token/cli.py create --user operator --tier admin --label admin-iphone"
