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

Load-average trigger added 2026-08-11: this guard was temperature-only
from the start, but a real 2026-08-11 test showed the box can be running
cool (57-66C, tier=0, nothing shed) while 1-min load climbs past 17 on
4 cores -- confirmed via common/llm.py's own pre-flight load-gate comment
("at load ~15 every model, including 1.5B, timed out regardless of
size", 2026-08-09) and a live ollama.service log showing a cold model
load losing the CPU race entirely (6m54s stuck in "waiting for
llama-server to become available", never reaching generation) with
temps nowhere near either thermal tier. Heat and CPU contention are
related but not the same signal -- a cool case with 4 ingest processes
saturating all 4 cores is exactly the gap the temp-only version missed.
Load tiers reuse the SAME tier1_feeds/tier2_feeds/resume mechanics as
temperature -- either signal can independently trip a given tier; RESUME
requires BOTH temp and load to be back under their resume thresholds
(stricter, to avoid flapping on whichever recovers first). Load values
are raw 1-min /proc/loadavg (not normalized per-core), matching the
convention common/llm.py's own OLLAMA_PREFLIGHT_LOAD_TARGET already uses
on this same 4-core box.

Tunables (dispatch.env, all optional -- defaults shown are what's used
if the var is absent or unparsable):
  THERMAL_GUARD_ENABLED=true
  THERMAL_GUARD_TIER1_TEMP_C=74.0
  THERMAL_GUARD_TIER2_TEMP_C=79.0
  THERMAL_GUARD_RESUME_TEMP_C=65.0
  THERMAL_GUARD_TIER1_LOAD=10.0
  THERMAL_GUARD_TIER2_LOAD=14.0
  THERMAL_GUARD_RESUME_LOAD=6.0
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


def get_load1():
    """Raw 1-min load average, or None (never raises) if unreadable."""
    try:
        with open("/proc/loadavg") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def get_fan_rpm():
    """Reads the real cooling fan by hwmon NAME ("pwmfan"), not a fixed
    hwmonN path -- numbering shifts once the dead gpio-fan overlay (Argon
    ONE leftover, always-max-duty, no physical fan attached) is removed
    from /boot/config.txt and the box reboots. Returns None if unreadable."""
    try:
        for entry in os.listdir("/sys/class/hwmon"):
            hw = f"/sys/class/hwmon/{entry}"
            try:
                with open(f"{hw}/name") as f:
                    if f.read().strip() != "pwmfan":
                        continue
                with open(f"{hw}/fan1_input") as f:
                    return int(f.read().strip())
            except (FileNotFoundError, ValueError):
                continue
    except FileNotFoundError:
        pass
    return None


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
    # Timeout must cover ingest-feed-ctl.sh's own staggered restart timing
    # (default 15s between each unit) plus real per-container start/stop
    # time, not just a flat guess. A flat 60s undercounted a 5-feed restart
    # (4 staggers alone = 60s) and caused the restart command to be killed
    # mid-batch on 2026-07-28, leaving itws down after an otherwise-clean
    # Tier 2 resume. Scale with target count instead.
    if target in ("all", "core"):
        n = 7 if target == "all" else 1
    else:
        n = len(target.split(","))
    budget = max(60, (n - 1) * 15 + n * 25)
    subprocess.run(
        [FEED_CTL, action, target],
        check=False, capture_output=True, text=True, timeout=budget,
    )


def _feed_is_active(feed):
    """True if the given ingest feed's systemd --user unit is active."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", f"corporatetraveldc-ingest-{feed}.service"],
            check=False, capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _reconcile_stale_tier(tier, tier1_feeds, tier2_feeds):
    """Correct a stale persisted tier after a host reboot.

    A reboot brings every enabled Quadlet back up regardless of what tier
    this guard last recorded (podman/systemd don't know about our JSON
    state file). Confirmed live 2026-07-26: after the Argon->DIY case
    swap + reboot, all 7 ingest containers came back up normally, but
    thermal_ingest_guard_state.json still said tier=2 from before the
    reboot -- which then silently blocked BOTH the trip logic (tier<2/
    tier<1 checks skip because tier was already "2") AND the resume logic
    (temp wasn't below resume_temp yet), leaving the guard inert instead
    of either shedding or restoring correctly. If the feeds implied by the
    persisted tier are actually running, the tier is stale -- reset to 0
    so this run's trip/resume logic evaluates fresh against the real
    container state instead of trusting last-known bookkeeping blindly.
    """
    if tier <= 0:
        return tier
    check_feeds = [f.strip() for f in tier1_feeds.split(",") if f.strip()]
    if tier >= 2:
        check_feeds += [f.strip() for f in tier2_feeds.split(",") if f.strip()]
    # Fixed 2026-07-27: was all(...) -- required EVERY tier-implicated feed
    # to be running before treating the tier as stale. That silently broke
    # in exactly the case this function exists for: an external intervention
    # (a manual `systemctl start` bypassing feed_ctl/this guard's own state
    # tracking -- e.g. a cooldown-restore script) brought most feeds back up
    # while one unrelated feed (itws) stayed down for its own separate
    # reason. all() then never matched, tier stayed stuck at 2 forever, and
    # since the trip logic only fires on tier<N transitions, the guard could
    # neither re-shed (tier already "2") nor resume (temp never held below
    # resume_temp long enough) -- fully inert in both directions while temps
    # climbed. any() is the correct invariant: if tier=N is accurate, NONE of
    # the N-implicated feeds should be running; if even one is up, the
    # record is stale regardless of what any other feed is doing.
    active_feeds = [f for f in check_feeds if _feed_is_active(f)]
    if active_feeds:
        print(f"{LOG_PREFIX} stale tier={tier} detected ({','.join(active_feeds)} actually running) -- resetting to 0")
        return 0
    return tier


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
    tier1_load = _float(cfg.get("THERMAL_GUARD_TIER1_LOAD"), 10.0)
    tier2_load = _float(cfg.get("THERMAL_GUARD_TIER2_LOAD"), 14.0)
    resume_load = _float(cfg.get("THERMAL_GUARD_RESUME_LOAD"), 6.0)
    resume_dwell = _float(cfg.get("THERMAL_GUARD_RESUME_DWELL_S"), 300)
    tier1_feeds = cfg.get("THERMAL_GUARD_TIER1_FEEDS", "tfms,stdds")
    tier2_feeds = cfg.get("THERMAL_GUARD_TIER2_FEEDS", "fdps,tbfm,itws")

    temp = get_temp_c()
    load1 = get_load1()
    load_str = f"{load1:.2f}" if load1 is not None else "n/a"
    fan_rpm = get_fan_rpm()
    fan_str = f"{fan_rpm}rpm" if fan_rpm is not None else "n/a"
    state = load_state()
    tier = state.get("tier", 0)
    tier = _reconcile_stale_tier(tier, tier1_feeds, tier2_feeds)
    if tier != state.get("tier", 0):
        state = {"tier": tier, "below_resume_since": None}
        save_state(state)
    now = time.time()

    print(f"{LOG_PREFIX} temp={temp:.2f}C load1={load_str} tier={tier} fan={fan_str}")

    temp2_trip = temp >= tier2_temp
    load2_trip = load1 is not None and load1 >= tier2_load
    temp1_trip = temp >= tier1_temp
    load1_trip = load1 is not None and load1 >= tier1_load

    if tier < 2 and (temp2_trip or load2_trip):
        feed_ctl("stop", tier2_feeds)
        if tier < 1:
            feed_ctl("stop", tier1_feeds)
        reason = " and ".join(
            r for r, hit in ((f"{temp:.1f}C", temp2_trip), (f"load {load_str}", load2_trip)) if hit
        )
        save_state({"tier": 2, "below_resume_since": None, "shed_at": now,
                     "peak_temp": temp, "peak_load1": load1, "peak_fan_rpm": fan_rpm})
        ntfy_alert(
            cfg,
            f"TIER 2: {reason} (fan {fan_str}) -- stopped {tier1_feeds},{tier2_feeds}. "
            f"Ollama governor and cooling not keeping up on their own.",
            "Thermal Guard -- TIER 2 shed", priority=5,
        )
        print(f"{LOG_PREFIX} tripped tier 2 ({reason}) fan={fan_str}")
        return

    if tier < 1 and (temp1_trip or load1_trip):
        feed_ctl("stop", tier1_feeds)
        reason = " and ".join(
            r for r, hit in ((f"{temp:.1f}C", temp1_trip), (f"load {load_str}", load1_trip)) if hit
        )
        save_state({"tier": 1, "below_resume_since": None, "shed_at": now,
                     "peak_temp": temp, "peak_load1": load1, "peak_fan_rpm": fan_rpm})
        ntfy_alert(
            cfg,
            f"TIER 1: {reason} (fan {fan_str}) -- stopped {tier1_feeds} to cut CPU load.",
            "Thermal Guard -- TIER 1 shed", priority=4,
        )
        print(f"{LOG_PREFIX} tripped tier 1 ({reason}) fan={fan_str}")
        return

    if tier > 0:
        # Resume requires BOTH signals to have recovered -- a cool box
        # still under heavy load, or a loaded-down box still hot, should
        # not restore feeds just because the OTHER signal cleared.
        temp_ok = temp < resume_temp
        load_ok = load1 is None or load1 < resume_load
        if temp_ok and load_ok:
            below_since = state.get("below_resume_since") or now
            state["below_resume_since"] = below_since
            if now - below_since >= resume_dwell:
                restored = tier1_feeds if tier == 1 else f"{tier1_feeds},{tier2_feeds}"
                feed_ctl("restart", restored)
                ntfy_alert(
                    cfg,
                    f"{temp:.1f}C / load {load_str} held below {resume_temp}C / "
                    f"{resume_load:.1f} for {resume_dwell:.0f}s -- restored {restored}.",
                    "Thermal Guard -- restored", priority=3,
                )
                print(f"{LOG_PREFIX} restored ({restored}) at {temp:.2f}C load={load_str}")
                state = {"tier": 0, "below_resume_since": None}
            save_state(state)
        elif state.get("below_resume_since") is not None:
            # Back above at least one resume threshold before the dwell
            # timer completed -- reset the dwell clock so a brief dip
            # doesn't trigger a premature/flapping restore.
            state["below_resume_since"] = None
            save_state(state)


if __name__ == "__main__":
    main()
