#!/usr/bin/env bash
# install.sh — corporatetraveldc-dispatch installer
# Supports: Linux x86_64, Linux aarch64 (ARM64), macOS x86_64 (Intel), macOS arm64 (Apple Silicon)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/CorporateTravelDC/corporatetraveldc-dispatch/main/install/install.sh | bash
#   -- or --
#   bash install/install.sh [--skip-ollama] [--skip-containers] [--dev]
#
# Flags:
#   --skip-ollama       Don't install or pull Ollama models (install Python stack only)
#   --skip-containers   Don't install Podman/Docker (bare Python mode — Android/Termux use install-android.sh)
#   --dev               Clone into ~/corporatetraveldc-dispatch instead of /opt/corporatetraveldc
#   --models "m1 m2"    Space-separated list of Ollama models to pull (default: llama3.2:3b mistral)

set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
SKIP_OLLAMA=false
SKIP_CONTAINERS=false
DEV_MODE=false
OLLAMA_MODELS="llama3.2:3b mistral"
REPO_URL="https://github.com/CorporateTravelDC/corporatetraveldc-dispatch.git"
INSTALL_DIR="/opt/corporatetraveldc"
SERVICE_USER="corporatetraveldc"
PYTHON_MIN="3.11"

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-ollama)     SKIP_OLLAMA=true ;;
        --skip-containers) SKIP_CONTAINERS=true ;;
        --dev)             DEV_MODE=true; INSTALL_DIR="$HOME/corporatetraveldc-dispatch" ;;
        --models)          OLLAMA_MODELS="$2"; shift ;;
        *) echo "Unknown flag: $1" >&2; exit 1 ;;
    esac
    shift
done

# ── Detect OS and architecture ────────────────────────────────────────────────
OS="$(uname -s)"   # Linux | Darwin
ARCH="$(uname -m)" # x86_64 | aarch64 | arm64

case "$OS" in
    Linux)
        case "$ARCH" in
            x86_64)  PLATFORM="linux-amd64" ;;
            aarch64) PLATFORM="linux-arm64" ;;
            *)       echo "Unsupported Linux arch: $ARCH" >&2; exit 1 ;;
        esac
        ;;
    Darwin)
        case "$ARCH" in
            x86_64) PLATFORM="macos-amd64" ;;
            arm64)  PLATFORM="macos-arm64" ;;
            *)      echo "Unsupported macOS arch: $ARCH" >&2; exit 1 ;;
        esac
        ;;
    *)
        echo "Unsupported OS: $OS. For Windows use install-windows.ps1; for Android use install-android.sh." >&2
        exit 1
        ;;
esac

echo "==> Platform: $PLATFORM"

# ── Detect Linux package manager ─────────────────────────────────────────────
PKG_MANAGER=""
if [[ "$OS" == "Linux" ]]; then
    if   command -v dnf  &>/dev/null; then PKG_MANAGER="dnf"
    elif command -v apt-get &>/dev/null; then PKG_MANAGER="apt"
    elif command -v pacman &>/dev/null; then PKG_MANAGER="pacman"
    elif command -v zypper &>/dev/null; then PKG_MANAGER="zypper"
    else
        echo "Warning: no supported package manager found. Install Python 3.11+ and git manually." >&2
    fi
fi

# ── Helper: require_cmd ───────────────────────────────────────────────────────
require_cmd() {
    command -v "$1" &>/dev/null || { echo "Required: $1 not found. Install it and retry." >&2; exit 1; }
}

# ── 1. System dependencies ────────────────────────────────────────────────────
echo ""
echo "==> Installing system dependencies..."

case "$PKG_MANAGER" in
    dnf)
        sudo dnf install -y python3 python3-pip python3-venv git curl libxml2-devel libxslt-devel gcc
        ;;
    apt)
        sudo apt-get update -qq
        sudo apt-get install -y python3 python3-pip python3-venv git curl \
            libxml2-dev libxslt1-dev build-essential
        ;;
    pacman)
        sudo pacman -Sy --noconfirm python python-pip git curl libxml2
        ;;
    zypper)
        sudo zypper install -y python3 python3-pip git curl libxml2-devel
        ;;
esac

if [[ "$OS" == "Darwin" ]]; then
    if ! command -v brew &>/dev/null; then
        echo "Homebrew not found. Installing..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    brew install python@3.11 git curl libxml2
fi

# ── 2. Python version check ───────────────────────────────────────────────────
echo ""
echo "==> Checking Python version..."
PYTHON_BIN=""
for py in python3.13 python3.12 python3.11 python3; do
    if command -v "$py" &>/dev/null; then
        VER="$($py -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        MAJOR="${VER%%.*}"; MINOR="${VER##*.}"
        if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 11 ]]; then
            PYTHON_BIN="$py"
            echo "    Using $py ($VER)"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    echo "Python $PYTHON_MIN+ required. Install it and retry." >&2
    exit 1
fi

# ── 3. Container runtime ──────────────────────────────────────────────────────
if [[ "$SKIP_CONTAINERS" == "false" ]]; then
    echo ""
    echo "==> Setting up container runtime..."

    if [[ "$OS" == "Linux" ]]; then
        if ! command -v podman &>/dev/null; then
            case "$PKG_MANAGER" in
                dnf)  sudo dnf install -y podman ;;
                apt)  sudo apt-get install -y podman ;;
                pacman) sudo pacman -Sy --noconfirm podman ;;
                zypper) sudo zypper install -y podman ;;
            esac
        fi
        echo "    Podman: $(podman --version)"
        # Enable rootless subuid/subgid for service user
        if id "$SERVICE_USER" &>/dev/null 2>&1; then
            sudo usermod --add-subuids 100000-165535 "$SERVICE_USER" 2>/dev/null || true
            sudo usermod --add-subgids 100000-165535 "$SERVICE_USER" 2>/dev/null || true
        fi

    elif [[ "$OS" == "Darwin" ]]; then
        if ! command -v podman &>/dev/null && ! command -v docker &>/dev/null; then
            echo "    Installing Podman Desktop for macOS..."
            brew install podman
            podman machine init
            podman machine start
        fi
        CONTAINER_CMD="$(command -v podman || command -v docker)"
        echo "    Container runtime: $CONTAINER_CMD"
    fi
fi

# ── 4. Ollama ─────────────────────────────────────────────────────────────────
if [[ "$SKIP_OLLAMA" == "false" ]]; then
    echo ""
    echo "==> Installing Ollama..."
    if ! command -v ollama &>/dev/null; then
        if [[ "$OS" == "Linux" ]]; then
            curl -fsSL https://ollama.ai/install.sh | sh
        elif [[ "$OS" == "Darwin" ]]; then
            if command -v brew &>/dev/null; then
                brew install ollama
            else
                echo "Download Ollama.app from https://ollama.com/download/mac and install it."
            fi
        fi
    else
        echo "    Ollama already installed: $(ollama --version 2>/dev/null || echo 'version unknown')"
    fi

    # Start Ollama service
    if [[ "$OS" == "Linux" ]]; then
        if systemctl is-enabled ollama &>/dev/null 2>&1 || systemctl is-active ollama &>/dev/null 2>&1; then
            sudo systemctl enable --now ollama
        else
            echo "    Note: 'ollama serve' must be running before pulling models."
            echo "    Start it with: sudo systemctl enable --now ollama"
        fi
    elif [[ "$OS" == "Darwin" ]]; then
        # macOS: Ollama.app auto-starts, or use 'ollama serve' in a terminal
        echo "    macOS: Open Ollama.app or run 'ollama serve' in a separate terminal."
    fi

    echo ""
    echo "==> Pulling Ollama models..."
    echo "    Models: $OLLAMA_MODELS"
    echo "    NOTE: No API keys required — all inference is local."
    echo "    Download sizes: llama3.2:3b ~2.0 GB | mistral ~4.1 GB | phi3.5 ~2.2 GB"
    echo "    llama3.1:8b ~4.7 GB | gemma2:9b ~5.5 GB | qwen2.5:7b ~4.7 GB"
    echo ""

    for MODEL in $OLLAMA_MODELS; do
        echo "    Pulling $MODEL..."
        ollama pull "$MODEL" || echo "    Warning: failed to pull $MODEL — pull it manually later."
    done
fi

# ── 5. Clone / update repo ────────────────────────────────────────────────────
echo ""
echo "==> Setting up repository at $INSTALL_DIR..."
if [[ -d "$INSTALL_DIR/.git" ]]; then
    echo "    Repo exists — pulling latest..."
    git -C "$INSTALL_DIR" pull
else
    if [[ "$DEV_MODE" == "true" ]]; then
        git clone "$REPO_URL" "$INSTALL_DIR"
    else
        sudo mkdir -p "$INSTALL_DIR"
        sudo chown "$USER":"$USER" "$INSTALL_DIR" 2>/dev/null || true
        git clone "$REPO_URL" "$INSTALL_DIR"
    fi
fi

# ── 6. Python venv + requirements ────────────────────────────────────────────
echo ""
echo "==> Creating Python virtual environment..."
cd "$INSTALL_DIR"
"$PYTHON_BIN" -m venv venv
venv/bin/pip install --upgrade pip wheel
venv/bin/pip install -r requirements.txt

echo ""
echo "==> Checking platform-specific package compatibility..."
echo "    Platform: $PLATFORM"

case "$PLATFORM" in
    linux-amd64)
        echo "    All packages: full support (prebuilt wheels)"
        echo "    SWIM ingest (solace-pubsubplus): supported"
        ;;
    linux-arm64)
        echo "    All core packages: prebuilt ARM64 wheels available"
        echo "    lxml: ARM64 wheel available via PyPI"
        echo "    solace-pubsubplus (SWIM ingest): must be built from source on ARM64"
        echo "      -> Run: pip install solace-pubsubplus --no-binary :all: (if NMS credentials provisioned)"
        echo "    pydantic-core: ARM64 wheel available"
        ;;
    macos-arm64)
        echo "    All core packages: Apple Silicon wheels available"
        echo "    solace-pubsubplus: Linux-only — SWIM ingest not supported on macOS"
        echo "      -> Run dispatch in a Linux VM or container for full SWIM support"
        ;;
    macos-amd64)
        echo "    All core packages: Intel macOS wheels available"
        echo "    solace-pubsubplus: Linux-only — SWIM ingest not supported on macOS"
        ;;
esac

# ── 7. Config files ────────────────────────────────────────────────────────────
echo ""
echo "==> Setting up configuration..."

if [[ "$OS" == "Linux" && "$DEV_MODE" == "false" ]]; then
    sudo mkdir -p /etc/corporatetraveldc /var/lib/corporatetraveldc
    if [[ ! -f /etc/corporatetraveldc/dispatch-secrets.env ]]; then
        sudo cp "$INSTALL_DIR/dispatch-secrets.env.example" /etc/corporatetraveldc/dispatch-secrets.env
        sudo chmod 0600 /etc/corporatetraveldc/dispatch-secrets.env
        echo "    Created /etc/corporatetraveldc/dispatch-secrets.env — edit to add credentials."
    fi
    if [[ ! -f /etc/corporatetraveldc/dispatch.env ]]; then
        echo "    Note: dispatch.env should be created by your firstboot script or manually."
        echo "    See dispatch.env.example in the repo root."
    fi
else
    # Dev mode / macOS — local config
    if [[ ! -f "$INSTALL_DIR/.env.local" ]]; then
        cp "$INSTALL_DIR/dispatch-secrets.env.example" "$INSTALL_DIR/.env.local"
        echo "    Created .env.local — edit to add credentials. Never commit this file."
    fi
fi

# ── 8. Modelfiles ─────────────────────────────────────────────────────────────
if [[ "$SKIP_OLLAMA" == "false" ]]; then
    echo ""
    echo "==> Checking for custom Modelfiles..."
    if [[ ! -f "$INSTALL_DIR/Modelfile.chat" ]]; then
        echo "    Modelfile.chat not found (it's .gitignored — operator-specific)."
        echo "    Copy and customize the template:"
        echo "      cp $INSTALL_DIR/Modelfile.chat.template $INSTALL_DIR/Modelfile.chat"
        echo "      # Edit Modelfile.chat with your operator context"
        echo "      ollama create csexec-chat -f $INSTALL_DIR/Modelfile.chat"
    else
        echo "    Building csexec-chat from Modelfile.chat..."
        ollama create csexec-chat -f "$INSTALL_DIR/Modelfile.chat" || true
    fi
    if [[ ! -f "$INSTALL_DIR/Modelfile.osint" ]]; then
        echo "    Modelfile.osint not found (it's .gitignored — operator-specific)."
        echo "    Copy and customize the template:"
        echo "      cp $INSTALL_DIR/Modelfile.osint.template $INSTALL_DIR/Modelfile.osint"
        echo "      # Edit Modelfile.osint with your operator context"
        echo "      ollama create csexec-osint -f $INSTALL_DIR/Modelfile.osint"
    else
        echo "    Building csexec-osint from Modelfile.osint..."
        ollama create csexec-osint -f "$INSTALL_DIR/Modelfile.osint" || true
    fi
fi

# ── 9. Build containers (Linux only) ─────────────────────────────────────────
if [[ "$OS" == "Linux" && "$SKIP_CONTAINERS" == "false" ]]; then
    echo ""
    echo "==> Building container images..."
    bash "$INSTALL_DIR/build-images.sh"
fi

# ── 10. Summary ───────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
echo "  corporatetraveldc-dispatch install complete"
echo "══════════════════════════════════════════════════════"
echo ""
echo "  Platform:    $PLATFORM"
echo "  Repo:        $INSTALL_DIR"
echo "  Python:      $PYTHON_BIN ($(${PYTHON_BIN} --version 2>&1 | awk '{print $2}'))"
echo "  Ollama:      $(command -v ollama &>/dev/null && ollama list 2>/dev/null | grep -c ':' || echo '0') model(s) installed"
echo ""
echo "  Next steps:"
echo "  1. Edit /etc/corporatetraveldc/dispatch-secrets.env — add credentials"
echo "  2. If on Linux: install Quadlets and enable systemd user services"
echo "       cp $INSTALL_DIR/.config/containers/systemd/*.container ~/.config/containers/systemd/"
echo "       systemctl --user daemon-reload"
echo "       systemctl --user start corporatetraveldc-web"
echo "  3. Verify: curl http://127.0.0.1:8000/healthz"
echo "  4. Customize Modelfile.chat and Modelfile.osint from templates"
echo ""
echo "  No LLM API key required — all inference is local via Ollama."
echo ""
