#!/bin/bash
# install/openwebui/install-openwebui.sh
#
# Deploys OpenWebUI as rootless Podman Quadlet.
# Run as corporatetraveldc, not root.
# Requires Ollama to be running first.
#
# IDEMPOTENT: yes

set -e

if [[ "$EUID" -eq 0 ]]; then
    echo "[FAIL] Run as corporatetraveldc, not root." >&2
    exit 1
fi

echo "=== install-openwebui.sh ==="

sudo loginctl enable-linger "$USER" 2>/dev/null || true

sudo mkdir -p /var/lib/openwebui
sudo chown "$USER:$USER" /var/lib/openwebui
echo "[OK]  /var/lib/openwebui ready"

QUADLET_DIR="$HOME/.config/containers/systemd"
mkdir -p "$QUADLET_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/openwebui.container" "$QUADLET_DIR/openwebui.container"
echo "[OK]  Quadlet deployed"

systemctl --user daemon-reload
systemctl --user enable --now openwebui.container
echo "[OK]  OpenWebUI started"

if command -v firewall-cmd &>/dev/null; then
    sudo firewall-cmd --permanent --zone=trusted --add-port=3000/tcp 2>/dev/null || true
    sudo firewall-cmd --reload
    echo "[OK]  firewalld: 3000/tcp open on trusted zone"
fi

echo ""
echo "-- OpenWebUI: http://100.94.80.100:3000"
echo "-- CF tunnel: https://openwebui.csexecutiveservices.com (when CF deployed)"
