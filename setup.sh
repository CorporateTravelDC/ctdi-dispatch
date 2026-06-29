#!/usr/bin/env bash
# setup.sh — corporatetraveldc dispatch system prep
# Prepares a Fedora (or Raspberry Pi OS) host for the dispatch stack.
# Run as root from the repo root: sudo bash setup.sh
#
# Assumes: podman, nginx, and systemctl are installed.
# Does NOT install packages, build images, or populate secrets.
# After this runs:
#   1. bash scripts/populate-secrets.sh   (as corporatetraveldc)
#   2. bash build-images.sh               (as corporatetraveldc)
#   3. systemctl --user start ...         (as corporatetraveldc)

set -euo pipefail

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; CYN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GRN}  ✓ $*${NC}"; }
info() { echo -e "${CYN}  · $*${NC}"; }
warn() { echo -e "${YLW}  ! $*${NC}"; }
die()  { echo -e "${RED}  ✗ $*${NC}" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash setup.sh"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo -e "${CYN}╔═══════════════════════════════════════════════════╗"
echo -e "║  corporatetraveldc dispatch — system setup v1.0   ║"
echo -e "╚═══════════════════════════════════════════════════╝${NC}"
echo ""

# ── preflight ─────────────────────────────────────────────────────────────────
for cmd in podman systemctl nginx; do
    command -v "$cmd" &>/dev/null || die "Required: $cmd not found. Install it first."
done
ok "Prerequisites OK"

# ── operator prompts ──────────────────────────────────────────────────────────
echo ""
echo -e "${CYN}  Site configuration (press Enter to accept defaults)${NC}"
echo ""
prompt() { local r; read -r -p "  $1 [$2]: " r; eval "$3=\"${r:-$2}\""; }

prompt "Public domain (e.g. example.com)"              "example.com"   DISPATCH_DOMAIN
prompt "Tailscale tailnet (e.g. example.ts.net)"       ""              TAILSCALE_TAILNET
prompt "Receiver latitude  (decimal)"                  "0.0000"        OP_LAT
prompt "Receiver longitude (decimal)"                  "0.0000"        OP_LON
prompt "Primary Amtrak station code"                   "WAS"           AMTRAK_HUB
prompt "NWS WFO filter (comma-separated, e.g. LWX)"   ""              WFO_FILTER
echo ""

# ── user ──────────────────────────────────────────────────────────────────────
DU=corporatetraveldc
DH="/home/${DU}"

if id "${DU}" &>/dev/null; then
    info "User ${DU} already exists"
else
    useradd --system --create-home --shell /bin/bash \
            --comment "dispatch daemon" "${DU}"
    ok "Created user ${DU}"
fi

# sub-uid/gid ranges for rootless Podman
for f in /etc/subuid /etc/subgid; do
    grep -q "^${DU}:" "$f" 2>/dev/null || \
        echo "${DU}:100000:65536" >> "$f"
done
ok "sub-uid/gid ranges confirmed"

# ── directories ───────────────────────────────────────────────────────────────
install -d -o "${DU}" -g "${DU}" -m 0755 \
    "${DH}/.config/containers/systemd" \
    "${DH}/.config/systemd/user" \
    "${DH}/.secrets"

install -d -o root -g root -m 0755 /etc/corporatetraveldc /etc/ntfy
install -d -o "${DU}" -g "${DU}" -m 0755 /var/lib/corporatetraveldc /var/lib/ntfy-backup
install -d -o "${DU}" -g "${DU}" -m 0700 /var/lib/ntfy-backup
ok "Directories created"

# ── tmpfiles rule for /run/corporatetraveldc ──────────────────────────────────
TMPF="${REPO_DIR}/systemd/tmpfiles.d/corporatetraveldc.conf"
if [[ -f "${TMPF}" ]]; then
    install -m 0644 "${TMPF}" /etc/tmpfiles.d/corporatetraveldc.conf
    systemd-tmpfiles --create /etc/tmpfiles.d/corporatetraveldc.conf 2>/dev/null || true
    ok "tmpfiles rule installed"
fi

# ── dispatch.env ──────────────────────────────────────────────────────────────
DENV=/etc/corporatetraveldc/dispatch.env
DENV_EXAMPLE="${REPO_DIR}/config/dispatch.env.example"
if [[ -f "${DENV}" ]]; then
    warn "${DENV} exists — skipping (delete to regenerate)"
elif [[ -f "${DENV_EXAMPLE}" ]]; then
    cp "${DENV_EXAMPLE}" "${DENV}"
    # Substitute operator-specific values
    [[ -n "${OP_LAT}"         ]] && sed -i "s|ULTRAFEEDER_LAT=.*|ULTRAFEEDER_LAT=${OP_LAT}|"                      "${DENV}"
    [[ -n "${OP_LON}"         ]] && sed -i "s|ULTRAFEEDER_LON=.*|ULTRAFEEDER_LON=${OP_LON}|"                      "${DENV}"
    [[ -n "${TAILSCALE_TAILNET}" ]] && sed -i "s|TAILSCALE_DOMAIN_SUFFIX=.*|TAILSCALE_DOMAIN_SUFFIX=.${TAILSCALE_TAILNET}|" "${DENV}"
    [[ -n "${AMTRAK_HUB}"     ]] && sed -i "s|AMTRAK_PRIMARY_STATION=.*|AMTRAK_PRIMARY_STATION=${AMTRAK_HUB}|"    "${DENV}"
    [[ -n "${WFO_FILTER}"     ]] && sed -i "s|NWWS_WFO_FILTER=.*|NWWS_WFO_FILTER=${WFO_FILTER}|"                 "${DENV}"
    chmod 0644 "${DENV}"
    ok "Created ${DENV}"
else
    warn "config/dispatch.env.example not found in repo — ${DENV} not created"
fi

# ── dispatch-secrets.env (template) ──────────────────────────────────────────
DSEC=/etc/corporatetraveldc/dispatch-secrets.env
DSEC_EXAMPLE="${REPO_DIR}/dispatch-secrets.env.example"
if [[ -f "${DSEC}" ]]; then
    warn "${DSEC} exists — skipping"
elif [[ -f "${DSEC_EXAMPLE}" ]]; then
    install -m 0600 -o root -g root "${DSEC_EXAMPLE}" "${DSEC}"
    ok "Created ${DSEC} (mode 0600) — fill in before starting the stack"
else
    warn "dispatch-secrets.env.example not found — ${DSEC} not created"
fi

# ── ntfy server.yml ───────────────────────────────────────────────────────────
NTFY_DST=/etc/ntfy/server.yml
NTFY_SRC="${REPO_DIR}/config/ntfy/server.yml"
if [[ -f "${NTFY_DST}" ]]; then
    warn "/etc/ntfy/server.yml exists — skipping"
elif [[ -f "${NTFY_SRC}" ]]; then
    install -m 0644 "${NTFY_SRC}" "${NTFY_DST}"
    [[ -n "${DISPATCH_DOMAIN}" ]] && \
        sed -i "s|base-url:.*|base-url: https://ntfy.${DISPATCH_DOMAIN}|" "${NTFY_DST}"
    ok "Installed /etc/ntfy/server.yml"
fi

# ── Quadlets ──────────────────────────────────────────────────────────────────
QSRC="${REPO_DIR}/systemd/quadlets"
QDST="${DH}/.config/containers/systemd"

if [[ -d "${QSRC}" ]]; then
    cp "${QSRC}"/*.container "${QDST}/" 2>/dev/null || true
    cp "${QSRC}"/*.network   "${QDST}/" 2>/dev/null || true
    cp "${QSRC}"/*.volume    "${QDST}/" 2>/dev/null || true
    # Timers live in user systemd, not containers/systemd
    cp "${QSRC}"/*.timer     "${DH}/.config/systemd/user/" 2>/dev/null || true
    chown -R "${DU}:${DU}" "${QDST}" "${DH}/.config/systemd/user"
    ok "Quadlets installed → ${QDST}"
else
    warn "systemd/quadlets/ not found in repo — copy Quadlets manually"
fi

# ── nginx vhosts ──────────────────────────────────────────────────────────────
NSRC="${REPO_DIR}/nginx/conf.d"
if [[ -d "${NSRC}" ]]; then
    installed=0
    for f in "${NSRC}"/*.conf; do
        [[ -f "$f" ]] || continue
        dest="/etc/nginx/conf.d/$(basename "$f")"
        [[ -f "${dest}" ]] && { warn "nginx: $(basename "$f") exists — skipping"; continue; }
        install -m 0644 "$f" "${dest}"
        (( installed++ ))
    done
    (( installed > 0 )) && ok "Installed ${installed} nginx vhost(s)"
    nginx -t 2>/dev/null && ok "nginx config valid" || warn "nginx config test failed — check /etc/nginx/conf.d/"
else
    warn "nginx/conf.d/ not found in repo — copy vhost configs manually"
fi

# ── watchdog ──────────────────────────────────────────────────────────────────
WATCHDOG_SH="${REPO_DIR}/scripts/watchdog.sh"
if [[ -f "${WATCHDOG_SH}" ]]; then
    install -m 0755 "${WATCHDOG_SH}" /usr/local/bin/ctdc-watchdog.sh

    # Write systemd units for watchdog timer + boot notify
    cat > /etc/systemd/system/ctdc-watchdog.service <<'UNIT'
[Unit]
Description=corporatetraveldc dispatch watchdog
After=network-online.target

[Service]
Type=oneshot
User=root
ExecStart=/usr/local/bin/ctdc-watchdog.sh
StandardOutput=journal
StandardError=journal
UNIT

    cat > /etc/systemd/system/ctdc-watchdog.timer <<'UNIT'
[Unit]
Description=corporatetraveldc dispatch watchdog (every 90s)
After=network-online.target

[Timer]
OnBootSec=3min
OnUnitActiveSec=90s

[Install]
WantedBy=timers.target
UNIT

    cat > /etc/systemd/system/ctdc-boot-notify.service <<'UNIT'
[Unit]
Description=corporatetraveldc boot notification
After=network-online.target ntfy.service

[Service]
Type=oneshot
User=corporatetraveldc
ExecStart=/usr/local/bin/ctdc-watchdog.sh
StandardOutput=journal

[Install]
WantedBy=multi-user.target
UNIT

    systemctl daemon-reload
    systemctl enable --now ctdc-watchdog.timer  2>/dev/null || true
    systemctl enable       ctdc-boot-notify.service 2>/dev/null || true
    ok "Watchdog installed and timer enabled"
else
    warn "scripts/watchdog.sh not found — watchdog not installed"
fi

# ── linger + daemon reload ────────────────────────────────────────────────────
loginctl enable-linger "${DU}"
ok "Linger enabled for ${DU}"

UID_DU="$(id -u "${DU}")"
XDG_RUN="/run/user/${UID_DU}"
if [[ -d "${XDG_RUN}" ]]; then
    sudo -u "${DU}" \
        XDG_RUNTIME_DIR="${XDG_RUN}" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUN}/bus" \
        systemctl --user daemon-reload 2>/dev/null && ok "User daemon reloaded" \
        || warn "daemon-reload failed — run 'systemctl --user daemon-reload' as ${DU} after first login"
else
    warn "User runtime dir not active yet — run 'systemctl --user daemon-reload' as ${DU} after login"
fi

# ── summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GRN}  Setup complete.${NC}"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Fill in secrets (as ${DU}):"
echo "       sudo machinectl shell ${DU}@"
echo "       vim /etc/corporatetraveldc/dispatch-secrets.env"
echo "       bash ${REPO_DIR}/scripts/populate-secrets.sh"
echo ""
echo "  2. Build container images:"
echo "       sudo machinectl shell ${DU}@"
echo "       cd ${REPO_DIR} && bash build-images.sh"
echo ""
echo "  3. Start the stack:"
echo "       systemctl --user start ntfy.service"
echo "       systemctl --user start corporatetraveldc-web.service"
echo "       systemctl --user start corporatetraveldc-poller.service"
echo "       systemctl --user start corporatetraveldc-pusher.service"
echo "       systemctl --user start corporatetraveldc-runner.service"
echo ""
echo "  4. Verify:"
echo "       curl http://localhost:18000/healthz"
echo ""
if [[ -n "${DISPATCH_DOMAIN}" && "${DISPATCH_DOMAIN}" != "example.com" ]]; then
echo "  5. Set up Cloudflare tunnel:"
echo "       dispatch.${DISPATCH_DOMAIN} → localhost:18000"
echo "       ntfy.${DISPATCH_DOMAIN}     → localhost:2586"
echo ""
fi
