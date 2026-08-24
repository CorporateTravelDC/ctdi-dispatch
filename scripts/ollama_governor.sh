#!/usr/bin/env bash
# ==============================================================================
# CTDI Dispatch Thermal Governor Installer - Fedora 40+ (SELinux Enforcing)
# Tailored for: Local Ollama Host + Rootless Podman Quadlet Stack
# ==============================================================================
#
# 2026-08-06: two changes from the version that shipped 2026-07-something:
#
# 1. Synced send_signal_to_ollama() to the live 2026-07-25 hotfix -- this
#    tracked copy had drifted from what was actually running on the box.
#    The original `pgrep -f ollama` matches against the FULL command line,
#    which also matches this script's own invocation ("python3
#    /usr/local/bin/ollama_governor.py" contains the substring "ollama"),
#    so the governor SIGSTOPped itself on every thermal trip along with
#    the real ollama process -- and since its own main loop was now
#    frozen, it could never detect cooldown or send SIGCONT, leaving it
#    stuck stopped indefinitely providing zero thermal protection. Fixed
#    live on 2026-07-25 (`pgrep -x` exact-match + explicit own-PID
#    exclusion) but never synced back here -- re-running this installer
#    as it stood would have silently reintroduced that bug.
#
# 2. MAX_TEMP/RECOVER_TEMP/CHECK_INTERVAL are now env-var configurable
#    (OLLAMA_GOVERNOR_MAX_TEMP_C / OLLAMA_GOVERNOR_RECOVER_TEMP_C /
#    OLLAMA_GOVERNOR_CHECK_INTERVAL_S), same os.getenv()-with-a-default
#    pattern as common/llm.py's OLLAMA_PREFLIGHT_COOL_* gate. Defaults
#    match today's hardcoded values exactly -- nothing changes for this
#    box unless /etc/corporatetraveldc/ollama-governor.env is created
#    (see config/ollama-governor.env for a documented template). This is
#    the actual point of the change: a deployment on lesser hardware
#    (older Pi, cost-constrained board with a lower safe thermal
#    ceiling) can retune these by editing that env file and running
#    `systemctl restart ollama-governor.service` -- no reinstall, no
#    rewriting this script, no SELinux policy regen needed.

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

# 2026-08-06: env-var configurable, defaults match the original hardcoded
# values -- see scripts/ollama_governor.sh header for the full rationale.
# Sourced via EnvironmentFile= in the systemd unit below (optional file,
# "-" prefix -- these os.getenv() defaults apply if it doesn't exist).
MAX_TEMP = float(os.getenv("OLLAMA_GOVERNOR_MAX_TEMP_C", "75.0"))          # Freeze Ollama at/above this temp (C)
RECOVER_TEMP = float(os.getenv("OLLAMA_GOVERNOR_RECOVER_TEMP_C", "68.0")) # Resume once at/below this temp (C)
CHECK_INTERVAL = float(os.getenv("OLLAMA_GOVERNOR_CHECK_INTERVAL_S", "2.0"))  # Sensor poll cadence (seconds)

if RECOVER_TEMP >= MAX_TEMP:
    raise SystemExit(
        f"[FATAL] OLLAMA_GOVERNOR_RECOVER_TEMP_C ({RECOVER_TEMP}) must be "
        f"lower than OLLAMA_GOVERNOR_MAX_TEMP_C ({MAX_TEMP}) -- as configured "
        f"the pause/resume state machine would never settle."
    )

OWN_PID = os.getpid()  # never signal ourselves

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
    """Finds the native host Ollama process and issues thread-freezing signals.

    FIX (2026-07-25): the original `pgrep -f ollama` matched against the full
    command line, which also matches THIS script's own invocation
    ("python3 /usr/local/bin/ollama_governor.py" contains the substring
    "ollama"). That caused the governor to SIGSTOP itself along with the real
    ollama process on every thermal trip -- and since its own main loop was
    now frozen, it could never detect cooldown or send SIGCONT, leaving it
    stuck stopped indefinitely (found 2026-07-25, had been dead for an
    unknown period providing zero thermal protection).

    Fix: use `pgrep -x ollama` (exact binary-name match -- only matches a
    process whose comm name is literally "ollama", i.e. `ollama serve`, never
    a python script that merely mentions the word) AND explicitly exclude our
    own PID as a second layer of defense against any future pattern change.
    """
    try:
        pid_strings = subprocess.check_output(["pgrep", "-x", "ollama"]).decode().strip().split('\n')
        for pid_str in pid_strings:
            pid = pid_str.strip()
            if pid.isdigit() and int(pid) != OWN_PID:
                os.kill(int(pid), sig)
    except subprocess.CalledProcessError:
        # pgrep exits 1 when it finds no matches -- ollama not running, nothing to do
        pass
    except Exception:
        pass

def main():
    is_paused = False
    print(
        f"[INFO] CTDI Ollama Governor initialized under Fedora SELinux policy "
        f"constraints. MAX_TEMP={MAX_TEMP}C RECOVER_TEMP={RECOVER_TEMP}C "
        f"CHECK_INTERVAL={CHECK_INTERVAL}s",
        flush=True,
    )

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
# 2026-08-06: optional env file ("-" prefix -- service starts fine with
# the script's own defaults if this file doesn't exist). See
# config/ollama-governor.env in the repo for a documented template.
EnvironmentFile=-/etc/corporatetraveldc/ollama-governor.env
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

# --- STEP 3.5: PREFER OLLAMA UNDER CONTENTION + AUTO-UNLOAD IDLE MODELS ---
echo "[+] Writing ollama.service resource-limit drop-in..."
mkdir -p /etc/systemd/system/ollama.service.d
cat << 'EOF' > /etc/systemd/system/ollama.service.d/20-resource-limits.conf
[Service]
# CPUWeight (cgroup v2 cpu.weight) instead of a static AllowedCPUs pin --
# weight only matters when something else is actually contending for CPU
# right now; the kernel scheduler re-evaluates every tick, live, with no
# polling daemon needed. Idle box: Ollama (and its llama-server children)
# can burst across all 4 cores for free. Under real contention with the
# dispatch containers (each at CPUWeight=100, see their .container files):
# 500 vs 100 means Ollama gets ~5x the contested CPU time -- clearly
# favored, without hard-starving the stack. This backstops the Modelfiles'
# own "PARAMETER num_thread 2" -- that only requests a thread count from
# llama.cpp, it does not by itself control OS-level scheduling priority.
CPUWeight=500

# Hard ceiling regardless of contention state -- no single inference run
# may ever claim more than 3 of the Pi's 4 cores, even if the box is
# otherwise completely idle. Same ceiling applied to every dispatch
# container (see CLAUDE.md "Container resource limits").
CPUQuota=300%

# Unload an idle model from memory after 10 minutes of no requests, so the
# Pi cools back toward baseline (60s-mid-60s C) between brief/OSINT runs
# instead of staying loaded (and hot) indefinitely.
Environment="OLLAMA_KEEP_ALIVE=10m"

# Memory cap, added 2026-07-31. Baseline B = ~3.1GB, the live cgroup
# memory.current measured while corporatetraveldc-pi5-osint (the model
# EP-advance/ops-brief use, num_ctx=4096, the biggest job on this box) was
# fully loaded with context allocated -- not an estimate, captured off a
# real run. Formula is operator-specified: 125% of B as the RAM-only
# comfort ceiling, 150% of B as the absolute combined RAM+swap ceiling.
#
# MemoryHigh = 125% of B (~3.9GB): soft threshold. Above this the kernel
# actively reclaims from this cgroup (page cache first, then swap) on an
# ongoing basis -- this is what keeps Ollama out of swap entirely under
# normal conditions, since with the rest of the box's RAM free, reclaim
# has cache to evict without touching swap.
MemoryHigh=3900M

# MemoryMax = 150% of B (~4.65GB): hard ceiling on the cgroup's own
# resident memory. Deliberately set above MemoryHigh so reclaim has room
# to work gracefully instead of hitting an immediate OOM wall.
MemoryMax=4650M

# MemorySwapMax: bounds how much of any overflow past MemoryHigh may
# land in swap specifically, so a contested-RAM scenario can push the
# model (or context) onto swap without ever swapping the entirety of it.
# 750M = the gap between the 125% and 150% marks.
MemorySwapMax=750M

# MemoryLow, added 2026-08-06. Protects Ollama's real working set (100%
# of B) from kernel reclaim as long as memory is available anywhere else
# on the box -- previously 0/unset, meaning an unrelated cgroup (e.g. the
# interactive desktop session) driving system-wide memory pressure could
# reclaim/swap Ollama with zero priority protection. See
# systemd/ollama.service.d/20-resource-limits.conf in this repo for the
# full incident writeup this was added to close.
MemoryLow=3100M
EOF

echo "[+] Reloading and restarting ollama.service with resource limits..."
systemctl daemon-reload
systemctl restart ollama.service

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
