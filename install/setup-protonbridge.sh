#!/usr/bin/env bash
# setup-protonbridge.sh — First-time Proton Bridge interactive setup
#
# What this does:
#   1. Ensures 'pass' is installed on the HOST (for reference; Bridge uses it inside container)
#   2. Pulls the container image
#   3. Runs the interactive login flow (OAuth + 2FA in terminal)
#   4. Persists credentials in the protonbridge-data named volume
#   5. Enables and starts the Quadlet service
#   6. Verifies SMTP port 1025 is accepting connections
#
# Run as: bash /opt/corporatetraveldc/ctdi-dispatch-internal/install/setup-protonbridge.sh
# Do NOT run as root — this is a rootless Podman service under corporatetraveldc.
#
# After setup, the SMTP relay is available at:
#   host: 127.0.0.1, port: 1025, user: <your ProtonMail address>
#   password: shown in Bridge output during setup (the SMTP bridge password, not your ProtonMail password)

set -euo pipefail

IMAGE="docker.io/schklom/protonmail-bridge:latest-arm64"
QUADLET="corporatetraveldc-protonbridge"

log()  { echo "[protonbridge-setup] $*"; }
die()  { echo "[protonbridge-setup] ERROR: $*" >&2; exit 1; }

[[ "$(id -un)" == "corporatetraveldc" ]] || die "Run as corporatetraveldc, not root"

# ── 1. Pull image ──────────────────────────────────────────────────────────────
log "Pulling Proton Bridge image (arm64)…"
podman pull "${IMAGE}" || die "Image pull failed"

# ── 2. Create named volume (idempotent) ───────────────────────────────────────
log "Ensuring protonbridge-data volume exists…"
podman volume inspect protonbridge-data &>/dev/null \
  || podman volume create protonbridge-data
log "  Volume: $(podman volume inspect protonbridge-data --format '{{.Mountpoint}}')"

# ── 3. Interactive login ───────────────────────────────────────────────────────
log ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "INTERACTIVE SETUP — you will be prompted to:"
log "  1. Accept the Proton Bridge license"
log "  2. Enter your ProtonMail email address"
log "  3. Enter your ProtonMail password"
log "  4. Complete 2FA if enabled"
log "  5. Note the BRIDGE SMTP PASSWORD shown at the end"
log "     (this is NOT your ProtonMail password — save it for msmtp config)"
log "  6. Type 'exit' when you see 'Bridge is ready'"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log ""
read -r -p "Press Enter to start the interactive session…"

podman run --rm -it \
  -v protonbridge-data:/root \
  "${IMAGE}"

log ""
log "Interactive session complete."

# ── 4. Enable and start the Quadlet ───────────────────────────────────────────
log "Enabling and starting ${QUADLET} service…"
systemctl --user daemon-reload
systemctl --user enable --now "${QUADLET}.service"
sleep 5

log "Service status:"
systemctl --user status "${QUADLET}.service" --no-pager | head -6

# ── 5. Verify SMTP port ────────────────────────────────────────────────────────
log "Checking SMTP port 1025…"
if timeout 5 bash -c 'echo QUIT | nc -q1 127.0.0.1 1025' 2>/dev/null | grep -q "220"; then
    log "✅ SMTP port 1025 is responding."
else
    log "⚠  SMTP port 1025 not yet responding. Wait 10–30s and check:"
    log "   systemctl --user logs ${QUADLET}"
fi

log ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "Next: add msmtp config at /etc/msmtprc (sudo required):"
log ""
log "  account proton"
log "  host 127.0.0.1"
log "  port 1025"
log "  auth on"
log "  user <your@proton.me>"
log "  password <bridge-smtp-password-from-setup>"
log "  tls off"
log "  tls_starttls off"
log "  from <your@proton.me>"
log ""
log "  account default : proton"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
