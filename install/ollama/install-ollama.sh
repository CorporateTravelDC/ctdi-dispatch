#!/bin/bash
# install/ollama/install-ollama.sh
#
# PURPOSE:
#   Installs Ollama on Fedora 43+ aarch64 (Pi 5).
#   Binds to loopback only -- Tailscale peers reach it via
#   the Tailscale IP which routes to loopback on the Pi.
#
# ARCHITECTURE:
#   Ollama API: machine-facing only
#     Local:        http://127.0.0.1:11434
#     Tailscale:    http://100.x.x.x:11434
#     CF tunnel:    RESERVED/403 -- never publicly routed
#
#   OpenWebUI: human-facing browser UI
#     Tailscale:    http://100.x.x.x:3000
#     CF tunnel:    https://openwebui.example.com
#
#   Claude Code / dispatch automated tasks use Ollama directly
#   via loopback. OpenWebUI is for human chat sessions only.
#
# MODEL:
#   This script does NOT pull models.
#   Pull after install: ollama pull qwen3:8b
#   Qwen3:8b is the recommended model for Pi 5 aarch64.
#
# IDEMPOTENT: yes -- safe to re-run

set -e

echo "=== install-ollama.sh ==="

if command -v ollama &>/dev/null; then
    echo "[OK]  Ollama already installed: $(ollama --version 2>/dev/null || echo unknown)"
else
    echo "[1/1] Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    echo "[OK]  Ollama installed"
fi

echo "[INFO] Configuring loopback-only binding..."
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/10-binding.conf > /dev/null << 'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
EOF
sudo systemctl daemon-reload

if ! systemctl is-active ollama &>/dev/null; then
    sudo systemctl enable --now ollama
    echo "[OK]  Ollama enabled and started"
else
    sudo systemctl restart ollama
    echo "[OK]  Ollama restarted"
fi

if command -v firewall-cmd &>/dev/null; then
    sudo firewall-cmd --permanent --zone=trusted --add-port=11434/tcp 2>/dev/null || true
    sudo firewall-cmd --reload
    echo "[OK]  firewalld: 11434/tcp open on trusted zone"
fi

# Set OLLAMA_HOST for dispatch user
BASHRC="$HOME/.bashrc"
if ! grep -q "OLLAMA_HOST" "$BASHRC" 2>/dev/null; then
    echo 'export OLLAMA_HOST=http://127.0.0.1:11434' >> "$BASHRC"
    echo "[OK]  OLLAMA_HOST added to ~/.bashrc"
else
    echo "[OK]  OLLAMA_HOST already in ~/.bashrc"
fi

echo ""
echo "-- Ollama ready at http://127.0.0.1:11434"
echo "-- Pull model when ready: ollama pull qwen3:8b"
