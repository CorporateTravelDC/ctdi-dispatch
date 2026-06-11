# install-windows.ps1 — corporatetraveldc-dispatch installer for Windows x64
# Requires: PowerShell 5.1+, Windows 10 2004+ or Windows 11 (for WSL2)
# Run as Administrator in PowerShell:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\install\install-windows.ps1
#
# Strategy: installs WSL2 (if missing) + Ollama for Windows, then runs install.sh
# inside WSL2. All Python services run in the Linux subsystem; Ollama runs natively
# on Windows and is accessible from WSL2 at the host IP.

param(
    [switch]$SkipWSL,
    [switch]$SkipOllama,
    [string]$WSLDistro = "Ubuntu-24.04",
    [string]$Models = "llama3.2:3b mistral"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step { param([string]$msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$msg) Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn { param([string]$msg) Write-Host "    WARNING: $msg" -ForegroundColor Yellow }

# ── Verify platform ───────────────────────────────────────────────────────────
Write-Step "Checking platform..."
$arch = $env:PROCESSOR_ARCHITECTURE
if ($arch -ne "AMD64") {
    Write-Error "This script requires Windows x64 (AMD64). Detected: $arch"
    exit 1
}

$winVer = [System.Environment]::OSVersion.Version
Write-Ok "Windows $($winVer.Major).$($winVer.Minor) ($arch)"

if ($winVer.Build -lt 19041) {
    Write-Error "WSL2 requires Windows 10 build 19041+ or Windows 11. Current build: $($winVer.Build)"
    exit 1
}

# ── Check Admin ───────────────────────────────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "Run this script as Administrator (right-click PowerShell -> Run as Administrator)."
    exit 1
}

# ── 1. WSL2 ──────────────────────────────────────────────────────────────────
if (-not $SkipWSL) {
    Write-Step "Setting up WSL2..."

    # Check if WSL2 feature is enabled
    $wslFeature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue
    $vmFeature  = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction SilentlyContinue

    if ($wslFeature.State -ne "Enabled" -or $vmFeature.State -ne "Enabled") {
        Write-Ok "Enabling WSL2 features (reboot may be required)..."
        Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart | Out-Null
        Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart | Out-Null
        Write-Warn "Features enabled. If prompted to reboot, do so and re-run this script."
    }

    # Install / set WSL2 as default
    $wslExe = "$env:SystemRoot\System32\wsl.exe"
    if (Test-Path $wslExe) {
        & $wslExe --set-default-version 2 2>&1 | Out-Null

        # Check if target distro is installed
        $installed = & $wslExe --list --quiet 2>&1
        if ($installed -notmatch [regex]::Escape($WSLDistro)) {
            Write-Ok "Installing $WSLDistro in WSL2..."
            & $wslExe --install -d $WSLDistro
            Write-Ok "WSL2 $WSLDistro installed. You may need to set a UNIX username/password on first launch."
        } else {
            Write-Ok "$WSLDistro already installed in WSL2."
        }
    } else {
        Write-Warn "wsl.exe not found. Install WSL2 manually: https://learn.microsoft.com/windows/wsl/install"
    }
}

# ── 2. Ollama for Windows ─────────────────────────────────────────────────────
if (-not $SkipOllama) {
    Write-Step "Installing Ollama for Windows..."

    $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollamaCmd) {
        $ollamaInstaller = "$env:TEMP\OllamaSetup.exe"
        Write-Ok "Downloading Ollama Windows installer..."
        Invoke-WebRequest -Uri "https://ollama.com/download/windows" -OutFile $ollamaInstaller -UseBasicParsing
        Write-Ok "Running installer (silent)..."
        Start-Process -FilePath $ollamaInstaller -ArgumentList "/S" -Wait
        $env:PATH = "$env:LOCALAPPDATA\Programs\Ollama;$env:PATH"
        Write-Ok "Ollama installed."
    } else {
        Write-Ok "Ollama already installed: $(ollama --version 2>&1)"
    }

    # Pull models
    Write-Step "Pulling Ollama models on Windows host..."
    Write-Ok "Models: $Models"
    Write-Ok "NOTE: No API keys required — all inference is local."
    Write-Ok "Download sizes: llama3.2:3b ~2.0 GB | mistral ~4.1 GB | phi3.5 ~2.2 GB"
    Write-Ok "               llama3.1:8b ~4.7 GB | gemma2:9b ~5.5 GB | qwen2.5:7b ~4.7 GB"

    foreach ($model in $Models.Split(" ")) {
        if ($model.Trim()) {
            Write-Ok "Pulling $model..."
            & ollama pull $model.Trim()
        }
    }

    # Ollama host accessible from WSL2
    Write-Ok ""
    Write-Ok "Ollama is running on the Windows host. From WSL2, reach it at:"
    Write-Ok "  OLLAMA_BASE_URL=http://$(Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias 'vEthernet (WSL)' -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty IPAddress):11434"
    Write-Ok "Or use the default: http://host.docker.internal:11434 (resolves in WSL2)"
}

# ── 3. Run install.sh inside WSL2 ─────────────────────────────────────────────
Write-Step "Running Linux install inside WSL2..."

$wslScript = @"
set -euo pipefail
echo '==> Updating WSL2 packages...'
sudo apt-get update -qq && sudo apt-get install -y git curl python3 python3-pip python3-venv \
    libxml2-dev libxslt1-dev build-essential 2>/dev/null

# Remap Ollama base URL to Windows host
export OLLAMA_BASE_URL="http://\$(ip route show | awk '/default/ {print \$3}'):11434"
echo "==> Ollama base URL (Windows host): \$OLLAMA_BASE_URL"

# Clone or update repo
INSTALL_DIR="\$HOME/corporatetraveldc-dispatch"
if [ -d "\$INSTALL_DIR/.git" ]; then
    git -C "\$INSTALL_DIR" pull
else
    git clone https://github.com/CorporateTravelDC/corporatetraveldc-dispatch-poc.git "\$INSTALL_DIR"
fi

cd "\$INSTALL_DIR"
python3 -m venv venv
venv/bin/pip install --upgrade pip wheel
venv/bin/pip install -r requirements.txt
echo ''
echo '==> WSL2 install complete.'
echo '    Edit \$HOME/corporatetraveldc-dispatch/.env.local to add credentials.'
echo '    Set OLLAMA_BASE_URL in dispatch.env to http://<windows-host-ip>:11434'
echo '    Run: PYTHONPATH=src venv/bin/python src/runner/main.py'
"@

# Write script to temp file and run in WSL2
$tmpScript = "$env:TEMP\ctdc_wsl_install.sh"
$wslScript | Out-File -FilePath $tmpScript -Encoding utf8 -NoNewline

$wslExe = "$env:SystemRoot\System32\wsl.exe"
& $wslExe --distribution $WSLDistro bash (& $wslExe wslpath ($tmpScript -replace '\\', '/')) 2>&1

Remove-Item $tmpScript -ErrorAction SilentlyContinue

# ── 4. Summary ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  corporatetraveldc-dispatch Windows install complete" -ForegroundColor Cyan
Write-Host "══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Architecture:  Windows x64 + WSL2 ($WSLDistro)"
Write-Host "  Ollama:        Running on Windows host (accessible from WSL2)"
Write-Host "  Python stack:  Installed in WSL2"
Write-Host ""
Write-Host "  Next steps:"
Write-Host "  1. Open WSL2: wsl -d $WSLDistro"
Write-Host "  2. cd ~/corporatetraveldc-dispatch"
Write-Host "  3. Edit .env.local — add credentials (FAA_NOTAM_API_KEY, NTFY_TOKEN, etc.)"
Write-Host "  4. Set OLLAMA_BASE_URL to point to Windows host in dispatch.env"
Write-Host "  5. Run: PYTHONPATH=src venv/bin/python src/runner/main.py"
Write-Host ""
Write-Host "  Container note: Podman/Docker Desktop optional for full container stack."
Write-Host "  No LLM API key required — Ollama runs locally on Windows."
Write-Host ""
