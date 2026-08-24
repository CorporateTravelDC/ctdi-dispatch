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

Load-average trigger added 2026-08-11, REDESIGNED 2026-08-23 (operator
directive after a night of real data): the original load1 tiers (10/14,
resume 6.0) were sized against a baseline that turned out to already sit
inside the trip band -- CLAUDE.md's own documented finding is "normal
load with the full stack up is 5-7", i.e. the resume threshold (6.0) was
inside normal noise, not comfortably above it. Confirmed live 2026-08-23:
every real trip on record has been load-driven, never temperature-driven
(see the TEMPERATURE paragraph below), and the 6.0 resume bar made
restoration rare even under genuinely idle conditions, because load1 is
a 1-minute EWMA that lags real state and any ordinary blip re-armed the
full 300s dwell.

New model, load only (temperature is UNCHANGED -- see below): load is
now a single-stage INFORMATIONAL-vs-LOCKDOWN split, not a two-tier
escalation. 15-40 is logged only, never sheds anything (this covers the
entire normal-to-busy range this box actually operates in, including
heavy multi-agent work). >=40 -- roughly 10x a healthy 4-core baseline,
not a marginal reading -- jumps straight to LOCKDOWN. Resume requires
load1 < 15 (not the old 6.0), held for the same dwell.

**LOCKDOWN, redefined 2026-08-23 (operator directive): "shed everything
in the stack, DDoS-lockdown style -- only localhost (web) survives."**
Not just the ingest feed groups anymore. When LOCKDOWN trips, this stops
ALL SIX real SWIM feeds (fdps/stdds/tfms/tbfm/itws/notam -- notam runs
the AIM/FNS feed, it is a real 6th SWIM feed, not a NOTAM-only
afterthought -- see docs/DATA_SOURCES.md), `ingest-core` (NWWS/Amtrak/
local airspace), `poller`, `pusher`, `runner`, AND `ollama.service`
itself (stopped via the standing NOPASSWD sudoers grant documented in
docs/SUDO_JUSTIFICATION_PROPOSAL.md -- confirmed live 2026-08-23, `sudo
-n systemctl {stop,start} ollama.service` needs no password). `web`
alone stays up, so /healthz and the API remain observable through the
whole event. This is deliberately more aggressive than the pre-2026-08-23
version, which only ever touched 5 of the 6 SWIM feeds and nothing else
-- the operator's own framing: an emergency this severe (roughly 10x
baseline load, or repeated genuine Ollama-contention fallbacks) calls
for shedding everything that could be contributing, not guessing at
which subset matters. See _lockdown_stop_stack()/_lockdown_start_stack()
below for the actual mechanics.

Ollama-fallback trigger, ALSO 2026-08-23: common/llm.py now records an
event to LOAD_FALLBACK_LOG whenever a generate() call fails for a reason
attributable to Ollama being CONTENDED specifically (slot-lock busy, or
a timeout talking to an already-loaded model) -- see
_record_load_fallback()'s docstring there for the exact attribution
rule, and why a deliberately-stopped Ollama does NOT count (that
attribution split matters even more now that LOCKDOWN itself stops
Ollama -- the fallback signal must never fire BECAUSE of a lockdown
that's already in progress, only because of genuine pre-lockdown
contention). If FALLBACK_TRIGGER_COUNT (default 2) or more such events
land inside FALLBACK_WINDOW_S (default 300s), that ALSO trips the same
LOCKDOWN as load>=40 -- a second, independent signal for "the box is
too contended for LLM work to succeed," catching cases raw load1 might
miss (e.g. contention concentrated in bursts the 1-minute average
smooths over).

TEMPERATURE is explicitly UNCHANGED in its own thresholds by this
redesign, but tier 2 (79C) now triggers the SAME full-stack LOCKDOWN
described above, not just a feed shed -- kept consistent with load/
fallback rather than carving out a narrower exception for temperature
specifically. Tier 1 (74C) remains the sole MILD stage, unchanged:
sheds only tier1_feeds (tfms,stdds). Confirmed live 2026-08-23 tier 2
has never once been the thing that actually tripped this guard in the
~6 days of journal history available (peak ever recorded: 71.05C,
comfortably under the 74C tier-1 line), and there's an independent,
auto-ramping PWM fan (see get_fan_rpm()) already providing real-time
thermal regulation underneath this script entirely on its own. Given
that margin, temperature keeps real teeth as the backstop for a
scenario that hasn't happened yet (fan degradation, ambient rise,
future workload growth) rather than being downgraded to informational
too -- unlike load, which this deployment's own data shows was
essentially always the actual, avoidable cause of feed downtime. A new
70-74C band is now logged as informational (approaching the real
trigger, not yet there) purely for visibility -- it changes no
behavior.

Original 2026-08-11 rationale, still why load matters at all: a real
test that day showed the box can be running cool (57-66C, tier=0,
nothing shed) while 1-min load climbs past 17 on 4 cores -- confirmed
via common/llm.py's own pre-flight load-gate comment ("at load ~15
every model, including 1.5B, timed out regardless of size", 2026-08-09)
and a live ollama.service log showing a cold model load losing the CPU
race entirely (6m54s stuck in "waiting for llama-server to become
available", never reaching generation) with temps nowhere near either
thermal tier. Heat and CPU contention are related but not the same
signal. Load values are raw 1-min /proc/loadavg (not normalized
per-core), matching the convention common/llm.py's own
OLLAMA_PREFLIGHT_LOAD_TARGET already uses on this same 4-core box.

Tunables (dispatch.env, all optional -- defaults shown are what's used
if the var is absent or unparsable):
  THERMAL_GUARD_ENABLED=true
  THERMAL_GUARD_TIER1_TEMP_C=74.0
  THERMAL_GUARD_TIER2_TEMP_C=79.0
  THERMAL_GUARD_TEMP_INFO_C=70.0
  THERMAL_GUARD_RESUME_TEMP_C=65.0
  THERMAL_GUARD_LOAD_INFO_MIN=15.0
  THERMAL_GUARD_LOAD_LOCKDOWN=40.0
  THERMAL_GUARD_RESUME_LOAD=15.0
  THERMAL_GUARD_RESUME_DWELL_S=300
  THERMAL_GUARD_FALLBACK_TRIGGER_COUNT=2
  THERMAL_GUARD_FALLBACK_WINDOW_S=300
  THERMAL_GUARD_TIER1_FEEDS=tfms,stdds
  THERMAL_GUARD_TIER2_FEEDS=fdps,tbfm,itws
"""
import json
import os
import subprocess
import time
import urllib.request

LOAD_FALLBACK_LOG = "/var/lib/corporatetraveldc/llm_load_fallback_events.jsonl"

DISPATCH_ENV = "/etc/corporatetraveldc/dispatch.env"
SECRETS_ENV = "/etc/corporatetraveldc/dispatch-secrets.env"
STATE_FILE = "/var/lib/corporatetraveldc/thermal_ingest_guard_state.json"
FEED_CTL = "/opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/ingest-feed-ctl.sh"
THERMAL_ZONE = "/sys/class/thermal/thermal_zone0/temp"
LOG_PREFIX = "thermal-ingest-guard:"

# 2026-08-23 LOCKDOWN redesign -- fixed, not tunable via dispatch.env on
# purpose. "Web survives, nothing else does" was a deliberate operator
# decision, not a default that should be casually overridden the way the
# temp/load thresholds are. ALL_SWIM_FEEDS is the real 6-feed set
# ingest-feed-ctl.sh's "all" target already expands to (fdps, stdds,
# tfms, tbfm, itws, notam -- notam runs the AIM/FNS feed, a real 6th
# SWIM feed, not a NOTAM-only afterthought). LOCKDOWN_USER_UNITS are the
# --user scope containers stopped alongside the feeds; web is
# deliberately absent from this list.
ALL_SWIM_FEEDS = ["fdps", "stdds", "tfms", "tbfm", "itws", "notam"]
LOCKDOWN_USER_UNITS = ["corporatetraveldc-poller", "corporatetraveldc-pusher", "corporatetraveldc-runner"]


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


def _user_unit_ctl(action, unit):
    """Stop/start/restart a --user scope systemd unit directly -- for the
    non-ingest members of LOCKDOWN_USER_UNITS (poller/pusher/runner).
    Best-effort, generous timeout, never raises -- matches feed_ctl()'s
    own fire-and-forget discipline (verification happens separately via
    _user_unit_is_active(), same pattern as _feed_is_active())."""
    try:
        subprocess.run(
            ["systemctl", "--user", action, f"{unit}.service"],
            check=False, capture_output=True, text=True, timeout=30,
        )
    except Exception:
        pass


def _user_unit_is_active(unit):
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", f"{unit}.service"],
            check=False, capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _ollama_ctl(action):
    """Stop/start ollama.service via the standing NOPASSWD sudoers grant
    (docs/SUDO_JUSTIFICATION_PROPOSAL.md: `corporatetraveldc ALL=(root)
    NOPASSWD: /usr/bin/systemctl restart ollama.service, start
    ollama.service, stop ollama.service`) -- confirmed live 2026-08-23.
    `-n` (non-interactive) so this NEVER hangs waiting for a password it
    will never get if the grant is somehow missing on a future box --
    fails fast and silently (best-effort, matches every other stack-control
    primitive here) instead of blocking the whole 2-minute guard cycle."""
    try:
        subprocess.run(
            ["sudo", "-n", "systemctl", action, "ollama.service"],
            check=False, capture_output=True, text=True, timeout=30,
        )
    except Exception:
        pass


def _ollama_is_active():
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "ollama.service"],
            check=False, capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _lockdown_stop_stack():
    """LOCKDOWN, 2026-08-23 redesign: shed the entire stack except web --
    all six real SWIM feeds, ingest-core, poller, pusher, runner, and
    Ollama itself. See the module docstring's LOCKDOWN paragraph for the
    "only localhost survives" rationale."""
    feed_ctl("stop", "all")   # 6 SWIM feeds: fdps,stdds,tfms,tbfm,itws,notam
    feed_ctl("stop", "core")  # NWWS-OI / Amtrak / local airspace
    for unit in LOCKDOWN_USER_UNITS:
        _user_unit_ctl("stop", unit)
    _ollama_ctl("stop")


def _lockdown_start_stack():
    feed_ctl("restart", "all")
    feed_ctl("restart", "core")
    for unit in LOCKDOWN_USER_UNITS:
        _user_unit_ctl("start", unit)
    _ollama_ctl("start")


def _lockdown_stack_down_list():
    """Everything in the LOCKDOWN scope that is NOT currently active --
    used both to verify a restart actually worked (empty list = clean)
    and by _reconcile_stale_tier's caller-adjacent checks. Named units,
    not booleans, so a RESTORE FAILED alert can say exactly what's still
    down instead of a generic failure."""
    down = [f"ingest-{f}" for f in ALL_SWIM_FEEDS if not _feed_is_active(f)]
    if not _feed_is_active("core"):
        down.append("ingest-core")
    down += [u for u in LOCKDOWN_USER_UNITS if not _user_unit_is_active(u)]
    if not _ollama_is_active():
        down.append("ollama.service")
    return down


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
        # 2026-08-23: tier 2 is now LOCKDOWN (see module docstring) --
        # implies ALL six SWIM feeds plus ingest-core down, not just the
        # old tier1_feeds+tier2_feeds pair. Checking only the old pair
        # here would let a stale tier=2 with a real ingest-notam or
        # ingest-core sitting active go undetected.
        check_feeds = list(ALL_SWIM_FEEDS) + ["core"]
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


# Retention for LOAD_FALLBACK_LOG -- generously wider than any real
# fallback_window_s so a rewritten/lowered tunable doesn't lose data it
# should still be able to see, but bounded so the file can't grow forever.
_FALLBACK_LOG_RETENTION_S = 3600.0


def count_recent_load_fallbacks(window_s):
    """Count load-attributed llm.py fallback events within the last
    window_s seconds, and prune anything older than
    _FALLBACK_LOG_RETENTION_S while we're already reading the file (same
    file, no separate cron needed). Never raises -- an unreadable/missing
    log just means zero events, not a crash of the whole guard cycle."""
    now = time.time()
    kept = []
    count = 0
    try:
        with open(LOAD_FALLBACK_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    ts = float(ev["ts"])
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
                if now - ts <= _FALLBACK_LOG_RETENTION_S:
                    kept.append(line)
                if now - ts <= window_s:
                    count += 1
    except FileNotFoundError:
        return 0

    try:
        tmp = LOAD_FALLBACK_LOG + ".tmp"
        with open(tmp, "w") as f:
            for line in kept:
                f.write(line + "\n")
        os.replace(tmp, LOAD_FALLBACK_LOG)
    except Exception:
        pass  # pruning is best-effort; the count above is already computed

    return count


def main():
    cfg = _cfg()
    if not _bool(cfg.get("THERMAL_GUARD_ENABLED"), True):
        return

    tier1_temp = _float(cfg.get("THERMAL_GUARD_TIER1_TEMP_C"), 74.0)
    tier2_temp = _float(cfg.get("THERMAL_GUARD_TIER2_TEMP_C"), 79.0)
    temp_info_c = _float(cfg.get("THERMAL_GUARD_TEMP_INFO_C"), 70.0)
    resume_temp = _float(cfg.get("THERMAL_GUARD_RESUME_TEMP_C"), 65.0)
    load_info_min = _float(cfg.get("THERMAL_GUARD_LOAD_INFO_MIN"), 15.0)
    load_lockdown = _float(cfg.get("THERMAL_GUARD_LOAD_LOCKDOWN"), 40.0)
    resume_load = _float(cfg.get("THERMAL_GUARD_RESUME_LOAD"), 15.0)
    resume_dwell = _float(cfg.get("THERMAL_GUARD_RESUME_DWELL_S"), 300)
    fallback_trigger_count = int(_float(cfg.get("THERMAL_GUARD_FALLBACK_TRIGGER_COUNT"), 2))
    fallback_window_s = _float(cfg.get("THERMAL_GUARD_FALLBACK_WINDOW_S"), 300)
    tier1_feeds = cfg.get("THERMAL_GUARD_TIER1_FEEDS", "tfms,stdds")
    tier2_feeds = cfg.get("THERMAL_GUARD_TIER2_FEEDS", "fdps,tbfm,itws")

    temp = get_temp_c()
    load1 = get_load1()
    load_str = f"{load1:.2f}" if load1 is not None else "n/a"
    fan_rpm = get_fan_rpm()
    fan_str = f"{fan_rpm}rpm" if fan_rpm is not None else "n/a"
    fallback_count = count_recent_load_fallbacks(fallback_window_s)
    state = load_state()
    tier = state.get("tier", 0)
    tier = _reconcile_stale_tier(tier, tier1_feeds, tier2_feeds)
    if tier != state.get("tier", 0):
        state = {"tier": tier, "below_resume_since": None}
        save_state(state)
    now = time.time()

    print(f"{LOG_PREFIX} temp={temp:.2f}C load1={load_str} tier={tier} fan={fan_str} "
          f"fallbacks={fallback_count}/{fallback_window_s:.0f}s")

    # 2026-08-23 redesign: temperature keeps its original two-stage real
    # trigger (see the module docstring for why -- it's never actually
    # been the cause of a trip in this box's history, but keeps real
    # teeth as an unattended-hardware backstop). Load collapses to a
    # single real trigger (lockdown, >= load_lockdown) plus an
    # independent fallback-count trigger -- either one jumps straight to
    # the full-shed state, matching temperature's tier-2 severity, since
    # anything that severe warrants no half-measure partial shed.
    temp2_trip = temp >= tier2_temp
    temp1_trip = temp >= tier1_temp
    load_lockdown_trip = load1 is not None and load1 >= load_lockdown
    fallback_trip = fallback_count >= fallback_trigger_count

    # Informational-only bands -- log for visibility, never shed anything.
    # Deliberately no ntfy push here: these bands cover this box's entire
    # normal-to-busy operating range (confirmed 2026-08-23 against real
    # history), so an alert on every 2-minute cycle spent in them would
    # just be noise, unlike the real trip/restore events below.
    if tier == 0:
        if temp_info_c <= temp < tier1_temp:
            print(f"{LOG_PREFIX} INFO: temp {temp:.1f}C in watch band "
                  f"[{temp_info_c:.0f}-{tier1_temp:.0f}C) -- approaching real trigger, no action")
        if load1 is not None and load_info_min <= load1 < load_lockdown:
            print(f"{LOG_PREFIX} INFO: load1 {load_str} in watch band "
                  f"[{load_info_min:.0f}-{load_lockdown:.0f}) -- normal-to-busy range, no action")

    if tier < 2 and (temp2_trip or load_lockdown_trip or fallback_trip):
        # 2026-08-23: LOCKDOWN sheds the whole stack (see module docstring
        # and _lockdown_stop_stack()) -- not just the feed groups anymore.
        _lockdown_stop_stack()
        reason = " and ".join(
            r for r, hit in (
                (f"{temp:.1f}C", temp2_trip),
                (f"load {load_str}", load_lockdown_trip),
                (f"{fallback_count} load-attributed brief fallbacks/{fallback_window_s:.0f}s", fallback_trip),
            ) if hit
        )
        # 2026-08-16: operator directive -- every real trip tonight has
        # been load, not temperature (box stayed well under trip temp the
        # whole time), but every alert said "Thermal Guard" regardless,
        # making it impossible to tell a genuine "I'm cooking" thermal
        # event (the actual prior incident this guard exists for) apart
        # from ordinary load-driven shedding at a glance. Title now
        # reflects what ACTUALLY tripped it, not a fixed "Thermal" label.
        # 2026-08-23: extended for the new fallback-count trigger.
        causes = [c for c, hit in (("Thermal", temp2_trip), ("Load", load_lockdown_trip),
                                    ("Ollama-contention", fallback_trip)) if hit]
        guard_label = "+".join(causes) + " Guard"
        save_state({"tier": 2, "below_resume_since": None, "shed_at": now,
                     "peak_temp": temp, "peak_load1": load1, "peak_fan_rpm": fan_rpm,
                     "guard_label": guard_label})
        ntfy_alert(
            cfg,
            f"LOCKDOWN: {reason} (fan {fan_str}) -- stopped the entire stack except web "
            f"(all 6 SWIM feeds, ingest-core, poller, pusher, runner, ollama.service). "
            f"Held until load < {resume_load:.0f}, temp < {resume_temp:.0f}C, and "
            f"fallbacks < {fallback_trigger_count}.",
            f"{guard_label} -- LOCKDOWN shed", priority=5,
        )
        print(f"{LOG_PREFIX} tripped LOCKDOWN ({reason}) fan={fan_str}")
        return

    if tier < 1 and temp1_trip:
        # Load no longer participates here -- see module docstring. A
        # sub-lockdown load reading (15-40) is informational only; load
        # only ever causes a shed via the full lockdown branch above.
        feed_ctl("stop", tier1_feeds)
        guard_label = "Thermal Guard"
        save_state({"tier": 1, "below_resume_since": None, "shed_at": now,
                     "peak_temp": temp, "peak_load1": load1, "peak_fan_rpm": fan_rpm,
                     "guard_label": guard_label})
        ntfy_alert(
            cfg,
            f"TIER 1: {temp:.1f}C (fan {fan_str}) -- stopped {tier1_feeds} to cut CPU load.",
            f"{guard_label} -- TIER 1 shed", priority=4,
        )
        print(f"{LOG_PREFIX} tripped tier 1 ({temp:.1f}C) fan={fan_str}")
        return

    if tier > 0:
        # Resume requires ALL signals to have recovered -- a cool box
        # still under heavy load, or a loaded-down box still hot, or one
        # still mid-fallback-storm, should not restore feeds just because
        # the OTHER signals cleared. 2026-08-23: added fallback_ok
        # alongside the original temp/load pair.
        temp_ok = temp < resume_temp
        load_ok = load1 is None or load1 < resume_load
        fallback_ok = fallback_count < fallback_trigger_count
        if temp_ok and load_ok and fallback_ok:
            below_since = state.get("below_resume_since") or now
            state["below_resume_since"] = below_since
            if now - below_since >= resume_dwell:
                # 2026-08-21: verify the restart actually worked before
                # declaring victory, rather than trusting feed_ctl()'s
                # discarded exit code. Root-caused 2026-08-21: a real
                # ~4h08m clean window produced zero fresh flight_events
                # writes because a restart call had silently failed and
                # nothing downstream ever checked. Give podman a moment
                # to actually start things, then verify for real.
                # 2026-08-23: tier 1 (mild, temp-only) still just restores
                # tier1_feeds the original way; tier 2 is now LOCKDOWN and
                # restores the WHOLE stack via _lockdown_start_stack(),
                # verified via _lockdown_stack_down_list().
                if tier == 1:
                    restored_desc = tier1_feeds
                    feed_ctl("restart", tier1_feeds)
                    time.sleep(5)
                    restored_feeds = [f.strip() for f in tier1_feeds.split(",") if f.strip()]
                    failed = [f for f in restored_feeds if not _feed_is_active(f)]
                else:
                    restored_desc = "the whole stack (all 6 SWIM feeds, ingest-core, poller, pusher, runner, ollama.service)"
                    _lockdown_start_stack()
                    time.sleep(5)
                    failed = _lockdown_stack_down_list()
                guard_label = state.get("guard_label", "Thermal/Load Guard")
                if failed:
                    ntfy_alert(
                        cfg,
                        f"Restart command ran but {','.join(failed)} did NOT come back "
                        f"up (verified via systemctl is-active). Conditions are fine "
                        f"({temp:.1f}C / load {load_str}) -- this is a real failure, not a "
                        f"thermal/load condition. Will retry restart next cycle.",
                        f"{guard_label} -- RESTORE FAILED", priority=5,
                    )
                    print(f"{LOG_PREFIX} restore FAILED for {failed} at {temp:.2f}C load={load_str}")
                    # Don't reset tier/below_resume_since -- conditions are
                    # still fine, so the next cycle retries the restart
                    # immediately rather than silently sitting shed.
                else:
                    # 2026-08-16: match the trip alert's label (temp vs load
                    # vs both), not a hardcoded "Thermal" -- see the
                    # trip-side comment above. Falls back to the generic
                    # label only for a state file written before this field
                    # existed.
                    ntfy_alert(
                        cfg,
                        f"{temp:.1f}C / load {load_str} / {fallback_count} fallbacks held below "
                        f"{resume_temp}C / {resume_load:.0f} / {fallback_trigger_count} for "
                        f"{resume_dwell:.0f}s -- restored {restored_desc} (verified active).",
                        f"{guard_label} -- restored", priority=3,
                    )
                    print(f"{LOG_PREFIX} restored ({restored_desc}) at {temp:.2f}C load={load_str}")
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
