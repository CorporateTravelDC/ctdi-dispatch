#!/usr/bin/env bash
# build-images.sh — builds all three Corporate Travel DC Dispatch container images
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

for service in web poller pusher ingest; do
    cf="Containerfile.${service}"
    tag="localhost/corporatetraveldc-${service}:latest"
    [[ -f "${cf}" ]] || die "${cf} not found"

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

log "All four images built successfully."
log ""
log "Next steps:"
log "  1. systemctl --user daemon-reload"
log "  2. systemctl --user start corporatetraveldc-poller"
log "  3. systemctl --user start corporatetraveldc-web"
log "  4. systemctl --user start corporatetraveldc-pusher"
log "  5. curl http://localhost:8000/healthz"
log "  6. csex-token create --user corey --tier admin --label admin-iphone"
