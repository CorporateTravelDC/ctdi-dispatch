#!/bin/bash
# session-restore.sh
# Run as corporatetraveldc on the Pi.
# Handles: git pull, secrets migration, image builds, Quadlet deploy,
#          service restarts, acars-net fix, health check.
# Usage: bash session-restore.sh [--dry-run]

set -euo pipefail

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

XDG_RUNTIME_DIR="/run/user/1000"
DBUS="unix:path=/run/user/1000/bus"
REPO="/opt/corporatetraveldc"
QUADLET_DIR="${HOME}/.config/containers/systemd"
SECRETS_DIR="${HOME}/.secrets"
SECRETS_ENV="/etc/corporatetraveldc/dispatch-secrets.env"
ENV_FILE="/etc/corporatetraveldc/dispatch.env"

run() {
    if [ "$DRY" -eq 1 ]; then
        echo "  [DRY] $*"
    else
        "$@"
    fi
}

svc() {
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR"     DBUS_SESSION_BUS_ADDRESS="$DBUS"     systemctl --user "$@"
}

log()  { echo ""; echo "=== $* ==="; }
ok()   { echo "  [OK] $*"; }
warn() { echo "  [WARN] $*"; }
fail() { echo "  [FAIL] $*"; }
skip() { echo "  [SKIP] $*"; }

# ----------------------------------------------------------------
log "Step 1: git pull"
# ----------------------------------------------------------------
cd "$REPO"

if ! git diff --quiet || ! git diff --cached --quiet; then
    warn "Unstaged changes detected -- stashing"
    run git stash
    STASHED=1
else
    STASHED=0
fi

run git pull
ok "git pull complete"

if [ "$STASHED" -eq 1 ]; then
    run git stash pop && ok "stash popped" || warn "stash pop had conflicts -- check git status"
fi

# ----------------------------------------------------------------
log "Step 2: Install pre-commit hook"
# ----------------------------------------------------------------
if [ -f "$REPO/scripts/pre-commit" ]; then
    run cp "$REPO/scripts/pre-commit" "$REPO/.git/hooks/pre-commit"
    run chmod +x "$REPO/.git/hooks/pre-commit"
    ok "pre-commit hook installed"
else
    warn "scripts/pre-commit not found -- skipping"
fi

# ----------------------------------------------------------------
log "Step 3: Secrets migration -- ~/.secrets/"
# ----------------------------------------------------------------
run mkdir -p "$SECRETS_DIR"
run chmod 700 "$SECRETS_DIR"

migrate_secret() {
    local env_key="$1"
    local secret_name="$2"
    local ext="${3:-token}"
    local dest="${SECRETS_DIR}/${secret_name}.${ext}"

    if [ -f "$dest" ] && [ -s "$dest" ]; then
        skip "${secret_name}.${ext} already present"
        return
    fi

    local val
    val=$(sudo grep -m1 "^${env_key}=" "$SECRETS_ENV" 2>/dev/null | cut -d= -f2- | tr -d "[:space:]") || true

    if [ -n "$val" ] && [ "$val" != "CHANGE_ME" ] && [ "$val" != "" ]; then
        if [ "$DRY" -eq 0 ]; then
            echo "$val" > "$dest"
            chmod 600 "$dest"
        fi
        ok "${secret_name}.${ext} written (${#val} chars)"
    else
        warn "${env_key} not set in secrets env -- ${secret_name}.${ext} skipped"
    fi
}

migrate_secret "ANTHROPIC_API_KEY"           "anthropic"
migrate_secret "NTFY_TOKEN"                  "ntfy"
migrate_secret "FAA_NOTAM_API_KEY"           "faa-notam"
migrate_secret "ACARSDRAMA_JUMPSEAT_TOKEN"   "acarsdrama"
migrate_secret "AIRFRAMES_TOKEN"             "airframes"
migrate_secret "MARINETRAFFIC_API_KEY"       "marinetraffic" "key"
migrate_secret "AIS_AISHUB_ID"              "aishub"          "key"
migrate_secret "AIS_VESSELFI_KEY"           "vesselfi"        "key"
migrate_secret "SWIM_USERNAME"              "swim-username"    "key"
migrate_secret "SWIM_PASSWORD"              "swim-password"    "key"

ok "Secrets dir: $(ls "$SECRETS_DIR" 2>/dev/null | wc -l) files"

# ----------------------------------------------------------------
log "Step 4: populate-secrets.sh"
# ----------------------------------------------------------------
if [ -f "$REPO/scripts/populate-secrets.sh" ]; then
    run bash "$REPO/scripts/populate-secrets.sh"
    ok "dispatch-secrets.env refreshed from ~/.secrets/"
else
    warn "populate-secrets.sh not found -- skipping"
fi

# ----------------------------------------------------------------
log "Step 5: Check build-images.sh for runner target"
# ----------------------------------------------------------------
BUILD_SCRIPT="$REPO/build-images.sh"
if grep -q "runner" "$BUILD_SCRIPT" 2>/dev/null; then
    ok "runner target already in build-images.sh"
else
    warn "runner not in build-images.sh -- appending"
    if [ "$DRY" -eq 0 ]; then
        cat >> "$BUILD_SCRIPT" << 'BUILDEOF'

# dispatch-runner (multi-stage: Node frontend + Python backend)
echo "Building corporatetraveldc-runner..."
podman build     -f Containerfile.runner     -t localhost/corporatetraveldc-runner:latest     .
BUILDEOF
    fi
    ok "runner build target appended"
fi

# ----------------------------------------------------------------
log "Step 6: Build container images"
# ----------------------------------------------------------------
cd "$REPO"
run bash build-images.sh
ok "All images built"

# ----------------------------------------------------------------
log "Step 7: Deploy Quadlets"
# ----------------------------------------------------------------
for qfile in     "$REPO/systemd/quadlets/corporatetraveldc-runner.container"     "$REPO/systemd/quadlets/corporatetraveldc-web.container"     "$REPO/systemd/quadlets/corporatetraveldc-poller.container"     "$REPO/systemd/quadlets/corporatetraveldc-pusher.container"
do
    fname=$(basename "$qfile")
    if [ -f "$qfile" ]; then
        run cp "$qfile" "$QUADLET_DIR/$fname"
        ok "deployed $fname"
    else
        warn "$fname not found in repo -- skipping"
    fi
done

# ----------------------------------------------------------------
log "Step 8: Fix acars-net -- ensure survives reboot"
# ----------------------------------------------------------------
# Create a systemd user service that recreates acars-net on login/boot
ACARS_NET_SERVICE="${HOME}/.config/systemd/user/acars-net-create.service"

if [ ! -f "$ACARS_NET_SERVICE" ]; then
    if [ "$DRY" -eq 0 ]; then
        mkdir -p "${HOME}/.config/systemd/user"
        cat > "$ACARS_NET_SERVICE" << 'SVCEOF'
[Unit]
Description=Create acars-net Podman network
Before=corporatetraveldc-acarshub.service
Before=corporatetraveldc-acarsrouter.service
Before=corporatetraveldc-dumpvdl2.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c 'podman network exists acars-net || podman network create acars-net'
ExecStop=/bin/true

[Install]
WantedBy=default.target
SVCEOF
    fi
    ok "acars-net-create.service written"
else
    skip "acars-net-create.service already present"
fi

# ----------------------------------------------------------------
log "Step 9: Reload systemd + restart services"
# ----------------------------------------------------------------
run svc daemon-reload
ok "daemon-reload complete"

RESTART_SVCS=(
    "acars-net-create.service"
    "corporatetraveldc-web.service"
    "corporatetraveldc-poller.service"
    "corporatetraveldc-pusher.service"
    "corporatetraveldc-runner.service"
)

if [ "$DRY" -eq 0 ]; then
    svc enable acars-net-create.service 2>/dev/null || true
fi

for svc_name in "${RESTART_SVCS[@]}"; do
    run svc restart "$svc_name" 2>/dev/null || warn "$svc_name restart failed -- may not exist yet"
done

sleep 8

# ----------------------------------------------------------------
log "Step 10: Health check"
# ----------------------------------------------------------------
ALL_OK=1

check_svc() {
    local name="$1"
    local status
    status=$(XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR"              DBUS_SESSION_BUS_ADDRESS="$DBUS"              systemctl --user is-active "$name" 2>/dev/null || echo "inactive")
    if [ "$status" = "active" ]; then
        ok "$name: active"
    else
        fail "$name: $status"
        ALL_OK=0
    fi
}

check_port() {
    local port="$1"
    local label="$2"
    if ss -tlnp 2>/dev/null | grep -q ":${port}"; then
        ok "port $port bound -- $label"
    else
        fail "port $port not bound -- $label"
        ALL_OK=0
    fi
}

check_svc "corporatetraveldc-web.service"
check_svc "corporatetraveldc-poller.service"
check_svc "corporatetraveldc-pusher.service"
check_svc "corporatetraveldc-runner.service"
check_svc "corporatetraveldc-ultrafeeder.service"
check_svc "corporatetraveldc-acarshub.service"
check_svc "corporatetraveldc-dumpvdl2.service"
check_svc "ntfy.service"

check_port 8000 "dispatch web"
check_port 8001 "dispatch runner"
check_port 8080 "ultrafeeder"
check_port 9081 "acarshub"
check_port 2586 "ntfy"

echo ""
echo "--- healthz ---"
curl -s --max-time 5 http://127.0.0.1:8000/healthz 2>/dev/null | python3 -m json.tool 2>/dev/null || warn "dispatch healthz unavailable"
echo ""
curl -s --max-time 5 http://127.0.0.1:8001/healthz 2>/dev/null | python3 -m json.tool 2>/dev/null || warn "runner healthz unavailable"

echo ""
if [ "$ALL_OK" -eq 1 ]; then
    echo "=== All checks passed ==="
else
    echo "=== Some checks failed -- review above ==="
fi

echo ""
echo "--- acars-net ---"
podman network ls | grep acars || warn "acars-net not found"

echo ""
echo "--- dispatch-runner tunnel ---"
echo "https://dispatch-runner.csexecutiveservices.com/healthz"
echo "(confirm accessible from Tailscale browser)"
