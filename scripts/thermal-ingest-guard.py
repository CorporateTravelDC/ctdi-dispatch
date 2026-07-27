#!/usr/bin/env python3
"""
thermal-ingest-guard.py -- automatic thermal/CPU-load fallback for SWIM
ingest containers.

Independent of, and does NOT touch, ollama_governor.py (Ollama's own
root-owned thermal SIGSTOP/SIGCONT mechanism at /usr/local/bin/). This
operates one level up, at the ingest-container level, reusing the same
stop/restart primitives as scripts/ingest-feed-ctl.sh (backlog fast-
forward triage included on restart, same as a manual restart would get).
Runs rootless as the corporatetraveldc user via a short-interval
systemd --user timer -- see
systemd/corporatetraveldc-thermal-ingest-guard.{service,timer}. No root
needed: /sys/class/thermal/thermal_zone0/temp is world-readable and
ingest containers are user-level Quadlets.

Added 2026-07-26 after a same-day incident: reseating the Pi into an
Argon ONE case ran hotter than the prior 40+ hours despite argononed's
fan confirmed pinned at its rated max RPM (5000) via a manually-pushed
aggressive fan curve, and manually stopping the five heaviest SWIM
containers (tfms/stdds/fdps/tbfm/itws -- ~68% combined CPU, tfms alone
was 39%) was the single most effective lever available remotely, more
effective than the CPU governor change (performance -> schedutil). This
makes that manual intervention a permanent, tunable, automatic safety
net regardless of what cooling hardware ends up in the case.

Threshold rationale: tier 1 trips at 74C, deliberately BELOW Ollama's
own governor pause point (75-76C, see ollama_governor.py) so ingest load
gets shed proactively and gives the box a chance to avoid an Ollama
pause entirely, rather than only reacting after Ollama has already
frozen. Tier 2 (79C) is the harder fallback if shedding tier 1 alone
isn't enough. Resume (65C, held 5 min) sits below Ollama's own 67.75C
resume point so restoration only happens once things are genuinely
settled, not right as Ollama itself is about to resume and add load
back.

Tunables (dispatch.env, all optional -- defaults shown are what's used
if the var is absent or unparsable):
  THERMAL_GUARD_ENABLED=true
  THERMAL_GUARD_TIER1_TEMP_C=74.0
  THERMAL_GUARD_TIER2_TEMP_C=79.0
  THERMAL_GUARD_RESUME_TEMP_C=65.0
  THERMAL_GUARD_RESUME_DWELL_S=300
  THERMAL_GUARD_TIER1_FEEDS=tfms,stdds
  THERMAL_GUARD_TIER2_FEEDS=fdps,tbfm,itws
"""
import json
import os
import subprocess
import time
import urllib.request

DISPATCH_ENV = "/etc/corporatetraveldc/dispatch.env"
SECRETS_ENV = "/etc/corporatetraveldc/dispatch-secrets.env"
STATE_FILE = "/var/lib/corporatetraveldc/thermal_ingest_guard_state.json"
FEED_CTL = "/opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/ingest-feed-ctl.sh"
THERMAL_ZONE = "/sys/class/thermal/thermal_zone0/temp"
LOG_PREFIX = "thermal-ingest-guard:"


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


def get_temp_c():
    with open(THERMAL_ZONE) as f:
        return float(f.read().strip()) / 1000.0


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"tier": 0, "below_resume_since": None}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def feed_ctl(action, target):
    subprocess.run(
        [FEED_CTL, action, target],
        check=False, capture_output=True, text=True, timeout=60,
    )


def _host_ntfy_base(cfg):
    """NTFY_URL in dispatch.env is written for pasta:--map-gw CONTAINERS
    (host.containers.internal alias) -- this script runs directly on the
    host, where that alias doesn't resolve to anything. ntfy publishes on
    all interfaces (see ntfy.container PublishPort), so the host reaches
    it over loopback on the same port NTFY_URL specifies."""
    base = cfg.get("NTFY_URL", "http://host.containers.internal:2586")
    port = base.rsplit(":", 1)[-1]
    return f"http://127.0.0.1:{port}"


def ntfy_alert(cfg, message, title, priority=4):
    base = _host_ntfy_base(cfg)
    token = cfg.get("NTFY_TOKEN", "").split(":")[0]
    url = f"{base}/ops-health"
    req = urllib.request.Request(url, data=message.encode("utf-8"), method="POST")
    req.add_header("Content-Type", "text/plain; charset=utf-8")
    req.add_header("X-Priority", str(priority))
    req.add_header("X-Title", title)
    req.add_header("X-Tags", "thermometer,warning")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"{LOG_PREFIX} ntfy push failed (non-fatal): {e}")


def main():
    cfg = _cfg()
    if not _bool(cfg.get("THERMAL_GUARD_ENABLED"), True):
        return

    tier1_temp = _float(cfg.get("THERMAL_GUARD_TIER1_TEMP_C"), 74.0)
    tier2_temp = _float(cfg.get("THERMAL_GUARD_TIER2_TEMP_C"), 79.0)
    resume_temp = _float(cfg.get("THERMAL_GUARD_RESUME_TEMP_C"), 65.0)
    resume_dwell = _float(cfg.get("THERMAL_GUARD_RESUME_DWELL_S"), 300)
    tier1_feeds = cfg.get("THERMAL_GUARD_TIER1_FEEDS", "tfms,stdds")
    tier2_feeds = cfg.get("THERMAL_GUARD_TIER2_FEEDS", "fdps,tbfm,itws")

    temp = get_temp_c()
    state = load_state()
    tier = state.get("tier", 0)
    now = time.time()

    print(f"{LOG_PREFIX} temp={temp:.2f}C tier={tier}")

    if tier < 2 and temp >= tier2_temp:
        feed_ctl("stop", tier2_feeds)
        if tier < 1:
            feed_ctl("stop", tier1_feeds)
        save_state({"tier": 2, "below_resume_since": None, "shed_at": now, "peak_temp": temp})
        ntfy_alert(
            cfg,
            f"TIER 2: {temp:.1f}C -- stopped {tier1_feeds},{tier2_feeds}. "
            f"Ollama governor and cooling not keeping up on their own.",
            "Thermal Guard -- TIER 2 shed", priority=5,
        )
        print(f"{LOG_PREFIX} tripped tier 2 at {temp:.2f}C")
        return

    if tier < 1 and temp >= tier1_temp:
        feed_ctl("stop", tier1_feeds)
        save_state({"tier": 1, "below_resume_since": None, "shed_at": now, "peak_temp": temp})
        ntfy_alert(
            cfg,
            f"TIER 1: {temp:.1f}C -- stopped {tier1_feeds} to cut CPU load.",
            "Thermal Guard -- TIER 1 shed", priority=4,
        )
        print(f"{LOG_PREFIX} tripped tier 1 at {temp:.2f}C")
        return

    if tier > 0:
        if temp < resume_temp:
            below_since = state.get("below_resume_since") or now
            state["below_resume_since"] = below_since
            if now - below_since >= resume_dwell:
                restored = tier1_feeds if tier == 1 else f"{tier1_feeds},{tier2_feeds}"
                feed_ctl("restart", restored)
                ntfy_alert(
                    cfg,
                    f"{temp:.1f}C held below {resume_temp}C for {resume_dwell:.0f}s -- "
                    f"restored {restored}.",
                    "Thermal Guard -- restored", priority=3,
                )
                print(f"{LOG_PREFIX} restored ({restored}) at {temp:.2f}C")
                state = {"tier": 0, "below_resume_since": None}
            save_state(state)
        elif state.get("below_resume_since") is not None:
            # Back above resume temp before the dwell timer completed --
            # reset the dwell clock so a brief dip doesn't trigger a
            # premature/flapping restore.
            state["below_resume_since"] = None
            save_state(state)


if __name__ == "__main__":
    main()
