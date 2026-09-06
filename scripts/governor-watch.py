#!/usr/bin/env python3
"""
governor-watch.py -- confirms the CPU frequency governor is still schedutil
and self-heals it if it has drifted, without disturbing a live inference run.

Added 2026-08-12 after finding the LIVE governor had silently drifted to
"performance" mid-session, even though the persistent boot-time config
(/etc/cpupower-service.conf: GOVERNOR=schedutil, applied by the enabled
cpupower.service unit) was correct the whole time and would have reasserted
schedutil on the next reboot. The drift's exact cause was never tracked down
-- this exists to catch and correct any future recurrence automatically
rather than relying on it being noticed by chance during an unrelated
diagnostic, and to never fight a live brief/inference run while doing so.

Runs rootless as the corporatetraveldc user via a 6-hour systemd --user
timer. The actual fix (`cpupower frequency-set -g schedutil`) needs root,
but that exact command is already a passwordless sudoers NOPASSWD entry on
this box (see `sudo -l`) -- no new privilege grant needed. Reading
/sys/devices/system/cpu/*/cpufreq/scaling_governor needs no privilege at
all.

Inference-awareness: before correcting a detected drift, checks for a live
`llama-server` child process (Ollama's actual inference engine) using
meaningful CPU -- if one is found, backs off and re-checks every 30s for up
to OLLAMA_GOVERNOR_WATCH_BACKOFF_S (default 900s / 15min) waiting for it to
finish, rather than changing the frequency-scaling policy mid-generation.
Bounded like every other wait in this codebase (thermal-ingest-guard.py's
resume dwell, common/llm.py's load/readiness gates) -- never blocks forever;
applies the fix anyway once the backoff window expires even if inference is
still running, since a governor change is a policy switch, not a process
signal, and is not expected to be disruptive the way SIGSTOP/SIGCONT are.

Tunables (dispatch.env, all optional -- defaults shown are what's used
if the var is absent or unparsable):
  GOVERNOR_WATCH_ENABLED=true
  GOVERNOR_WATCH_TARGET=schedutil
  GOVERNOR_WATCH_BACKOFF_S=900
  GOVERNOR_WATCH_POLL_S=30
  GOVERNOR_WATCH_CPU_BUSY_PCT=15.0
"""
import glob
import os
import subprocess
import time
import urllib.request

DISPATCH_ENV = "/etc/corporatetraveldc/dispatch.env"
SECRETS_ENV = "/etc/corporatetraveldc/dispatch-secrets.env"
GOVERNOR_GLOB = "/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"
LOG_PREFIX = "governor-watch:"


def _parse_env_file(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return out


def _cfg():
    env = {}
    env.update(_parse_env_file(DISPATCH_ENV))
    env.update(_parse_env_file(SECRETS_ENV))
    return env


def _bool(v, default):
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def get_governors():
    """dict of {cpu_path: governor_string} for every core found."""
    out = {}
    for path in sorted(glob.glob(GOVERNOR_GLOB)):
        try:
            with open(path) as f:
                out[path] = f.read().strip()
        except (FileNotFoundError, PermissionError):
            continue
    return out


def inference_active(busy_pct):
    """True if a llama-server child process is using meaningful CPU right
    now -- the actual inference engine, not just `ollama serve` sitting
    idle-resident within its keep-alive window."""
    try:
        r = subprocess.run(
            ["ps", "-eo", "comm,%cpu", "--no-headers"],
            check=False, capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return False  # can't tell -- don't block the fix on a bad ps call
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        comm, cpu = parts[0], parts[-1]
        if "llama-server" not in comm:
            continue
        try:
            if float(cpu) >= busy_pct:
                return True
        except ValueError:
            continue
    return False


def _host_ntfy_base(cfg):
    """Same host-vs-container NTFY_URL resolution as thermal-ingest-guard.py
    -- this script also runs directly on the host, not in a container."""
    base = cfg.get("NTFY_URL", "http://host.containers.internal:2586")
    port = base.rsplit(":", 1)[-1]
    return f"http://127.0.0.1:{port}"


def ntfy_alert(cfg, message, title, priority=3):
    base = _host_ntfy_base(cfg)
    token = cfg.get("NTFY_TOKEN", "").split(":")[0]
    url = f"{base}/ops-health"
    req = urllib.request.Request(url, data=message.encode("utf-8"), method="POST")
    req.add_header("Content-Type", "text/plain; charset=utf-8")
    req.add_header("X-Priority", str(priority))
    req.add_header("X-Title", title)
    req.add_header("X-Tags", "gear,warning")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"{LOG_PREFIX} ntfy push failed (non-fatal): {e}")


def apply_governor(target):
    """cpupower frequency-set -g <target> via the pre-approved passwordless
    sudoers entry -- no new grant needed, see module docstring."""
    r = subprocess.run(
        ["sudo", "-n", "/usr/bin/cpupower", "frequency-set", "-g", target],
        check=False, capture_output=True, text=True, timeout=30,
    )
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def main():
    cfg = _cfg()
    if not _bool(cfg.get("GOVERNOR_WATCH_ENABLED"), True):
        return

    target = cfg.get("GOVERNOR_WATCH_TARGET", "schedutil")
    backoff_s = _float(cfg.get("GOVERNOR_WATCH_BACKOFF_S"), 900.0)
    poll_s = _float(cfg.get("GOVERNOR_WATCH_POLL_S"), 30.0)
    busy_pct = _float(cfg.get("GOVERNOR_WATCH_CPU_BUSY_PCT"), 15.0)

    governors = get_governors()
    if not governors:
        print(f"{LOG_PREFIX} no scaling_governor files readable -- nothing to check")
        return

    drifted = {p: g for p, g in governors.items() if g != target}
    if not drifted:
        print(f"{LOG_PREFIX} OK -- all {len(governors)} cores at '{target}'")
        return

    print(f"{LOG_PREFIX} DRIFT: {len(drifted)}/{len(governors)} core(s) not at "
          f"'{target}': {drifted}")

    waited = 0.0
    while inference_active(busy_pct) and waited < backoff_s:
        print(f"{LOG_PREFIX} live inference detected (llama-server busy) -- "
              f"backing off {poll_s:.0f}s ({waited:.0f}/{backoff_s:.0f}s waited)")
        time.sleep(poll_s)
        waited += poll_s

    if waited >= backoff_s and inference_active(busy_pct):
        print(f"{LOG_PREFIX} backoff window ({backoff_s:.0f}s) expired, "
              f"inference still active -- applying fix anyway")

    ok, output = apply_governor(target)
    governors_after = get_governors()
    still_drifted = {p: g for p, g in governors_after.items() if g != target}

    if ok and not still_drifted:
        print(f"{LOG_PREFIX} corrected -- all cores now at '{target}'")
        ntfy_alert(
            cfg,
            f"CPU governor drifted to {sorted(set(drifted.values()))} on "
            f"{len(drifted)} core(s), corrected back to '{target}' "
            f"(waited {waited:.0f}s for live inference to clear).",
            "Governor Watch -- drift corrected", priority=3,
        )
    else:
        print(f"{LOG_PREFIX} FIX FAILED -- rc/output: {output!r}, "
              f"still drifted: {still_drifted}")
        ntfy_alert(
            cfg,
            f"CPU governor drift detected ({drifted}) but the correction "
            f"command failed: {output!r}. Still drifted: {still_drifted}. "
            f"Needs manual attention.",
            "Governor Watch -- FIX FAILED", priority=5,
        )


if __name__ == "__main__":
    main()
