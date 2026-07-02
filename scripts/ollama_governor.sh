#!/usr/bin/env bash
# ==============================================================================
# CTDI Dispatch Thermal Governor Installer - Fedora 40+ (SELinux Enforcing)
# Tailored for: Local Ollama Host + Rootless Podman Quadlet Stack
# ==============================================================================

set -e

# Ensure script is run as root
if [ "$EUID" -ne 0 ]; then
  echo "[-] Please run this script as root (sudo)."
  exit 1
fi

echo "[+] Installing required Fedora policy tools..."
dnf install -y policycoreutils-python-utils checkpolicy /usr/bin/pgrep

# --- STEP 1: CREATE THE PYTHON GOVERNOR ---
echo "[+] Writing python governor script to /usr/local/bin/ollama_governor.py..."
cat << 'EOF' > /usr/local/bin/ollama_governor.py
#!/usr/bin/env python3
import os
import subprocess
import time
import signal

MAX_TEMP = 75.0      # Freeze Ollama at 75oC to protect CTDI dispatch loop
RECOVER_TEMP = 68.0  # Safe temperature threshold to resume
CHECK_INTERVAL = 2.0 # Check hardware sensors every 2 seconds

def get_pi_temperature():
    """Reads the hwmon matrix matching Fedora kernel architecture."""
    try:
        for i in range(10): 
            name_path = f"/sys/class/hwmon/hwmon{i}/name"
            if os.path.exists(name_path):
                with open(name_path, "r") as f:
                    driver_name = f.read().strip()
                    if "cpu_thermal" in driver_name or "bcm" in driver_name:
                        with open(f"/sys/class/hwmon/hwmon{i}/temp1_input", "r") as tf:
                            return float(tf.read().strip()) / 1000.0
        # Fallback path
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return float(f.read().strip()) / 1000.0
    except IOError:
        return 0.0

def send_signal_to_ollama(sig):
    """Finds the native host Ollama process and issues thread-freezing signals."""
    try:
        # Match 'ollama' broadly to find 'ollama serve' running natively on the host
        pid_strings = subprocess.check_output(["pgrep", "-f", "ollama"]).decode().strip().split('\n')
        for pid_str in pid_strings:
            pid = pid_str.strip()
            if pid.isdigit():
                os.kill(int(pid), sig)
    except Exception:
        pass

def main():
    is_paused = False
    print("[INFO] CTDI Ollama Governor initialized under Fedora SELinux policy constraints.", flush=True)
    
    while True:
        current_temp = get_pi_temperature()
        if current_temp >= MAX_TEMP and not is_paused:
            send_signal_to_ollama(signal.SIGSTOP)
            is_paused = True
            print(f"[ALERT] Temperature hit {current_temp}oC! Pausing native Ollama processing threads.", flush=True)
        elif current_temp <= RECOVER_TEMP and is_paused:
            send_signal_to_ollama(signal.SIGCONT)
            is_paused = False
            print(f"[INFO] Temperature cooled to {current_temp}oC. Resuming local inference.", flush=True)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
EOF

chmod +x /usr/local/bin/ollama_governor.py

# --- STEP 2: CREATE THE SYSTEMD UNIT ---
echo "[+] Creating systemd service file..."
cat << 'EOF' > /etc/systemd/system/ollama-governor.service
[Unit]
Description=CTDI Dispatch Thermal Governor for Host Ollama Engine
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/bin/ollama_governor.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

# --- STEP 3: BUILD CUSTOM SELINUX POLICY ---
echo "[+] Generating custom SELinux Type Enforcement Module..."
mkdir -p /tmp/ctdi_governor_selinux && cd /tmp/ctdi_governor_selinux

cat << 'EOF' > ctdi_governor.te
module ctdi_governor 1.0;

require {
    type init_t;
    type sysfs_t;
    class file { read open getattr };
    class dir { read open search };
    class process { signal signull };
}

# 1. Allow the systemd service (init_t) to crawl /sys/class/hwmon for CPU metrics
allow init_t sysfs_t:dir { read open search };
allow init_t sysfs_t:file { read open getattr };

# 2. Allow systemd script layer to broadcast STOP/CONT signals to the host Ollama processes
allow init_t init_t:process { signal signull };
EOF

echo "[+] Compiling and injecting SELinux module package..."
checkmodule -M -m -o ctdi_governor.mod ctdi_governor.te
semodule_package -o ctdi_governor.pp -m ctdi_governor.mod
semodule -i ctdi_governor.pp

# --- STEP 4: APPLY CONTEXTS AND LAUNCH ---
echo "[+] Applying correct target context file labeling via restorecon..."
restorecon -v /usr/local/bin/ollama_governor.py
restorecon -v /etc/systemd/system/ollama-governor.service

echo "[+] Loading and starting thermal governor..."
systemctl daemon-reload
systemctl enable ollama-governor.service
systemctl restart ollama-governor.service

# Clean up
rm -rf /tmp/ctdi_governor_selinux
echo "[SUCCESS] The customized CTDI thermal safety layer is deployed and enforcing!"
