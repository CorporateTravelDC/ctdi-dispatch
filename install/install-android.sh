#!/data/data/com.termux/files/usr/bin/bash
# install-android.sh — corporatetraveldc-dispatch installer for Android ARM64
# Requires: Termux (https://termux.dev) — install from F-Droid, not the Play Store
#
# Architecture: Android ARM64 (aarch64) only
# Container note: Podman/Docker are not supported in Termux.
#                 Services run as bare Python processes managed by Termux:Boot.
#
# Usage (inside Termux):
#   curl -fsSL https://raw.githubusercontent.com/CorporateTravelDC/corporatetraveldc-dispatch-poc/main/install/install-android.sh | bash
#   -- or --
#   bash install/install-android.sh [--skip-ollama] [--models "llama3.2:3b mistral"]

set -euo pipefail

# ── Verify we're in Termux on ARM64 ──────────────────────────────────────────
if [[ ! -d "/data/data/com.termux" ]]; then
    echo "This script must run inside Termux on Android." >&2
    echo "Install Termux from F-Droid: https://f-droid.org/packages/com.termux/" >&2
    exit 1
fi

ARCH="$(uname -m)"
if [[ "$ARCH" != "aarch64" ]]; then
    echo "Android install supports aarch64 only. Detected: $ARCH" >&2
    exit 1
fi

# ── Defaults ──────────────────────────────────────────────────────────────────
SKIP_OLLAMA=false
OLLAMA_MODELS="llama3.2:3b mistral"
INSTALL_DIR="$HOME/corporatetraveldc-dispatch"
REPO_URL="https://github.com/CorporateTravelDC/corporatetraveldc-dispatch-poc.git"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-ollama) SKIP_OLLAMA=true ;;
        --models) OLLAMA_MODELS="$2"; shift ;;
        *) echo "Unknown flag: $1" >&2; exit 1 ;;
    esac
    shift
done

echo "==> Platform: Android ARM64 (Termux)"
echo "==> Note: containers not supported — services will run as bare Python processes"

# ── 1. Termux packages ────────────────────────────────────────────────────────
echo ""
echo "==> Installing Termux packages..."
pkg update -y
pkg install -y python git curl libxml2 libxslt clang make

# Termux-specific: install pip
pip install --upgrade pip wheel 2>/dev/null || python -m ensurepip --upgrade

# ── 2. Python venv + requirements ─────────────────────────────────────────────
echo ""
echo "==> Cloning repository..."
if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -C "$INSTALL_DIR" pull
else
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

echo ""
echo "==> Creating Python virtual environment..."
cd "$INSTALL_DIR"
python -m venv venv
venv/bin/pip install --upgrade pip wheel

echo ""
echo "==> Installing Python dependencies (ARM64 / Termux)..."

# Core packages — install from PyPI (arm64 wheels available for most)
venv/bin/pip install \
    "fastapi>=0.111.0" \
    "uvicorn>=0.29.0" \
    "pydantic>=2.7.0" \
    "sse-starlette>=1.6.5" \
    "requests>=2.32.0" \
    "httpx>=0.27" \
    "slixmpp>=1.8"

# lxml on Termux needs to build from source using Termux's libxml2/libxslt
echo "    Building lxml from source (uses Termux libxml2)..."
venv/bin/pip install "lxml>=5.0" \
    --no-binary lxml \
    CFLAGS="-I${PREFIX}/include" \
    LDFLAGS="-L${PREFIX}/lib" || {
    echo "    Warning: lxml build failed. XML parsing features may be limited."
    echo "    Try: pkg install libxml2 libxslt && pip install lxml --no-binary lxml"
}

# solace-pubsubplus (SWIM ingest): Linux-native C library, not supported in Termux
echo "    Note: solace-pubsubplus (FAA SWIM NMS ingest) is not supported in Termux."
echo "    SWIM push feeds will not be available on Android. REST poll fallback works."

# ── 3. Ollama on Android ──────────────────────────────────────────────────────
if [[ "$SKIP_OLLAMA" == "false" ]]; then
    echo ""
    echo "==> Installing Ollama for Android ARM64..."

    OPTION_A_AVAILABLE=false
    OPTION_B_AVAILABLE=false

    # Option A: Ollama CLI via Termux package (preferred if available)
    if pkg show ollama &>/dev/null 2>&1; then
        pkg install -y ollama
        OPTION_A_AVAILABLE=true
        echo "    Installed via Termux package."

    # Option B: Official ARM64 binary
    elif curl -fsSL --head https://ollama.com/download/ollama-linux-arm64 | grep -q "200\|302"; then
        OLLAMA_BIN="$PREFIX/bin/ollama"
        echo "    Downloading official ARM64 binary..."
        curl -fsSL "https://ollama.com/download/ollama-linux-arm64" -o "$OLLAMA_BIN"
        chmod +x "$OLLAMA_BIN"
        OPTION_B_AVAILABLE=true
        echo "    Installed to $OLLAMA_BIN"

    else
        echo "    Note: Ollama binary not found for this Android version."
        echo "    Alternatives:"
        echo "    1. Use the Ollama Android app (if available for your device)"
        echo "    2. Run Ollama on a separate machine and point OLLAMA_BASE_URL to it"
        echo "       (e.g., a Raspberry Pi, Mac, or Windows PC on the same network)"
        echo "    3. Install via Termux if pkg install ollama becomes available"
    fi

    if command -v ollama &>/dev/null; then
        echo ""
        echo "==> Starting Ollama server in background..."
        nohup ollama serve > "$HOME/ollama.log" 2>&1 &
        echo "    PID: $!  |  Log: $HOME/ollama.log"
        sleep 3

        echo ""
        echo "==> Pulling models..."
        echo "    NOTE: No API keys required — all inference is local."
        echo "    Recommended for Android (low RAM): llama3.2:3b (~2.0 GB) or phi3.5 (~2.2 GB)"
        echo "    Full OSINT model: mistral (~4.1 GB) — needs 6+ GB free storage"
        for MODEL in $OLLAMA_MODELS; do
            echo "    Pulling $MODEL..."
            ollama pull "$MODEL" || echo "    Warning: failed to pull $MODEL"
        done
    fi
fi

# ── 4. Config ─────────────────────────────────────────────────────────────────
echo ""
echo "==> Setting up configuration..."
if [[ ! -f "$INSTALL_DIR/.env.local" ]]; then
    cp "$INSTALL_DIR/dispatch-secrets.env.example" "$INSTALL_DIR/.env.local"
    echo "    Created .env.local — edit to add credentials. Never commit this file."
fi

# ── 5. Termux:Boot startup script ─────────────────────────────────────────────
echo ""
echo "==> Creating Termux:Boot startup script..."
mkdir -p "$HOME/.termux/boot"
cat > "$HOME/.termux/boot/corporatetraveldc.sh" << 'BOOT'
#!/data/data/com.termux/files/usr/bin/bash
# Auto-started by Termux:Boot on Android reboot
# Install Termux:Boot from F-Droid to enable this

cd ~/corporatetraveldc-dispatch

# Start Ollama
if command -v ollama &>/dev/null; then
    nohup ollama serve > ~/ollama.log 2>&1 &
    sleep 5
fi

# Load environment
set -a
[ -f .env.local ] && source .env.local
[ -f /etc/corporatetraveldc/dispatch.env ] && source /etc/corporatetraveldc/dispatch.env
set +a

export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export PYTHONPATH=src

# Start web API
nohup venv/bin/python src/runner/main.py > ~/dispatch-web.log 2>&1 &

# Start poller
nohup venv/bin/python src/poller/scheduler.py > ~/dispatch-poller.log 2>&1 &

echo "corporatetraveldc services started (bare Python, no containers)"
BOOT
chmod +x "$HOME/.termux/boot/corporatetraveldc.sh"
echo "    Boot script created: ~/.termux/boot/corporatetraveldc.sh"
echo "    Install Termux:Boot from F-Droid to enable auto-start on reboot."

# ── 6. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
echo "  corporatetraveldc-dispatch Android install complete"
echo "══════════════════════════════════════════════════════"
echo ""
echo "  Platform:    Android ARM64 (Termux)"
echo "  Repo:        $INSTALL_DIR"
echo "  Mode:        Bare Python (no containers)"
echo ""
echo "  Supported features on Android:"
echo "    ✓ Web API (FastAPI / uvicorn)"
echo "    ✓ Scheduler / poller"
echo "    ✓ REST feed polling (METAR, NWS, ATCSCC, TFR, NOTAMs)"
echo "    ✓ Push alerts via ntfy"
echo "    ✓ Local LLM via Ollama (if installed)"
echo "    ✓ Dispatch web UI accessible from phone browser"
echo "    ✗ FAA SWIM NMS push ingest (solace-pubsubplus, Linux-native C library)"
echo "    ✗ systemd Quadlets (use Termux:Boot instead)"
echo ""
echo "  iOS / iPadOS note: Run the stack on any supported platform above,"
echo "  then browse to https://dispatch.csexecutiveservices.com (or local IP)"
echo "  from Safari on iPhone/iPad. No server-side install supported on iOS."
echo ""
echo "  Manual start:"
echo "    cd $INSTALL_DIR"
echo "    ollama serve &               # if using local Ollama"
echo "    PYTHONPATH=src venv/bin/python src/runner/main.py &"
echo "    PYTHONPATH=src venv/bin/python src/poller/scheduler.py &"
echo ""
echo "  No LLM API key required — all inference is local via Ollama."
echo ""
