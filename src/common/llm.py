"""
common.llm — Shared LLM inference with Ollama-first / Anthropic fallback.

Priority:
  1. Ollama  (OLLAMA_BASE_URL set and reachable)
  2. Anthropic API  (ANTHROPIC_API_KEY set, AND both gates below open)
  3. None  (caller uses its own deterministic fallback)

Two independent gates control step 2, both must be open:
  - Per-call: generate()'s allow_anthropic param (default True). A caller
    that has no real fallback of its own passes the default; ops_brief.py
    passes False as of 2026-08-06 (it has a deterministic template).
    ep_advance_brief.py never goes through this function at all -- it
    calls ollama_post_with_retry() directly, so it never had Anthropic
    access to gate.
  - Global: ANTHROPIC_FALLBACK_ENABLED env var (default "true" -- this
    module ships as a template other deployments may run hybrid
    local+cloud, so the out-of-the-box default preserves that with zero
    config needed). 2026-08-06: THIS box's dispatch.env sets it to
    "false" -- operator directive is no Anthropic/cloud calls at all
    from this deployment, across every caller (route_impact,
    tfr_enrichment, osint_monitor, weekly_summary, aam_weekly_watch,
    dispatch_desk_memo, second_brain_daily/weekly,
    transport_pattern_digest -- everything, not just the two briefs).
    Deliberately a separate flag from allow_anthropic rather than
    flipping that parameter's own default to False -- changing the
    per-call default would silently change behavior for every other
    deployment of this codebase; this env var only changes it for boxes
    that explicitly set it.

Usage:
    from common.llm import generate

    text = generate(
        system="You are a concise aviation dispatcher.",
        prompt="Summarise this TFR: ...",
        ollama_model="corporatetraveldc-pi5-osint:latest",
        max_tokens=200,
        temperature=0.2,
    )
    if text is None:
        text = deterministic_fallback(...)
"""

import inspect
import logging
import os
import pathlib
import subprocess
import time

import httpx

from common.ollama_lock import ollama_slot, OllamaBusyError

# Signed-manifest integrity gate (docs/COMPLIANCE_SECURITY.md "Signed
# Manifest Integrity") -- see _verify_before_inference() below, called at
# the top of ollama_post_with_retry() (the one function every LLM call path
# converges on, including ep_advance_brief.py's direct call that bypasses
# generate() entirely -- see that function's own docstring).
class IntegrityCheckFailed(RuntimeError):
    """Raised when a calling skill's source file or a model's Modelfile
    doesn't match its signed hash. Deliberately NOT swallowed into
    generate()'s normal None-return/deterministic-fallback contract --
    a failed integrity check is a security event, not an ordinary
    Ollama-unavailable condition, and should be as loud as a crash, not as
    quiet as a timeout."""


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_VERIFY_SCRIPT = _REPO_ROOT / "scripts" / "verify-manifest.sh"
_verified_this_process: set[str] = set()


def _verify_integrity(relpath: str) -> None:
    """Verify one file against the signed manifest, once per process
    (cached in _verified_this_process -- each skill run is a fresh
    short-lived process anyway, so this is effectively once per run, not a
    per-call cost). Raises IntegrityCheckFailed on any failure: missing
    verify script, bad signature, hash mismatch, or the path simply not
    being in the manifest at all."""
    if relpath in _verified_this_process:
        return
    if not _VERIFY_SCRIPT.exists():
        raise IntegrityCheckFailed(
            f"llm: {_VERIFY_SCRIPT} not found -- cannot verify {relpath}, refusing to proceed"
        )
    result = subprocess.run(
        [str(_VERIFY_SCRIPT), relpath],
        capture_output=True, text=True, cwd=str(_REPO_ROOT),
    )
    if result.returncode != 0:
        raise IntegrityCheckFailed(
            f"llm: integrity check failed for {relpath}: {result.stderr.strip() or result.stdout.strip()}"
        )
    _verified_this_process.add(relpath)


def _modelfile_relpath_for(ollama_model: str | None) -> str | None:
    """Derive corporatetraveldc.<suffix> from a corporatetraveldc-pi5-<suffix>
    model name, matching build-models.sh's own naming convention. Returns
    None for anything that doesn't match (a bare upstream model like
    "gemma3:4b" pulled directly -- not one of ours, nothing to check)."""
    if not ollama_model:
        return None
    name = ollama_model.split(":", 1)[0]
    prefix = "corporatetraveldc-pi5-"
    if not name.startswith(prefix):
        return None
    suffix = name[len(prefix):]
    return f"corporatetraveldc.{suffix}"


def _verify_before_inference(ollama_model: str | None) -> None:
    """Called at the top of ollama_post_with_retry(). Verifies (1) the
    skill file that ultimately triggered this call -- the first stack
    frame outside this module, so it correctly identifies the real caller
    whether it came via generate()/_ollama() (all inside this file) or a
    skill calling ollama_post_with_retry() directly -- and (2) the
    Modelfile for a dedicated corporatetraveldc-pi5-* model, if the name
    matches that convention."""
    this_file = str(pathlib.Path(__file__).resolve())
    caller_file = None
    for frame_info in inspect.stack():
        candidate = str(pathlib.Path(frame_info.filename).resolve())
        if candidate != this_file:
            caller_file = candidate
            break
    if caller_file:
        try:
            caller_relpath = str(pathlib.Path(caller_file).relative_to(_REPO_ROOT))
            _verify_integrity(caller_relpath)
        except ValueError:
            # Caller isn't under the repo root at all (e.g. an interactive
            # REPL/test outside the checked-out tree) -- nothing to verify
            # against, and nothing to block either.
            pass

    modelfile_relpath = _modelfile_relpath_for(ollama_model)
    if modelfile_relpath:
        _verify_integrity(modelfile_relpath)

log = logging.getLogger(__name__)

OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "").rstrip("/")
OLLAMA_TIMEOUT    = int(os.getenv("OLLAMA_TIMEOUT", "900"))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Haiku is the Anthropic fallback — fast and cheap for short skill outputs.
ANTHROPIC_FALLBACK_MODEL = "claude-haiku-4-5-20251001"

# Global master gate (2026-08-06) -- see module docstring above for the
# full two-gate design. Same boolean-parsing style already used for
# OLLAMA_PREFLIGHT_COOL_ENABLED below. Default "true" so this module
# behaves exactly as it always has for any deployment that doesn't set
# this var -- this box's own dispatch.env sets it to "false".
ANTHROPIC_FALLBACK_ENABLED = os.getenv("ANTHROPIC_FALLBACK_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

# ── Pause-aware readiness wait (added 2026-07-27) ─────────────────────────────
# ollama_governor.py SIGSTOPs the native `ollama serve` process on a thermal
# trip (~75C) and SIGCONTs it back around 65-68C. A SIGSTOPped process does
# not refuse connections or error -- it just never responds, so a plain
# generate() call sits fully silent until ITS OWN timeout expires and only
# THEN falls back. Real pauses have run up to ~19 minutes; our timeouts are
# tuned to real per-skill p99 generation time (60s-1200s depending on prompt
# size, see OLLAMA_TIMEOUT usage note in generate()) and were never meant to
# also absorb a governor pause on top of normal inference time.
#
# The fix is NOT "wait out the whole pause" (that would blow every caller's
# timeout budget for a 19-minute event). It's: do a cheap, bounded poll for
# readiness FIRST, carved OUT of the caller's existing timeout rather than
# stacked additively on top of it. If Ollama comes back within that bounded
# slice, proceed to generate with whatever time remains. If it doesn't,
# skip straight to the Anthropic/deterministic fallback -- same total worst
# case as before (never exceeds effective_timeout), but the common case
# (governor not paused) costs nothing extra because the first readiness
# check succeeds immediately.
OLLAMA_READY_TIMEOUT_S      = float(os.getenv("OLLAMA_READY_TIMEOUT_S", "3.0"))
OLLAMA_READY_POLL_INTERVAL_S = float(os.getenv("OLLAMA_READY_POLL_INTERVAL_S", "3.0"))
OLLAMA_READY_WAIT_FRACTION  = float(os.getenv("OLLAMA_READY_WAIT_FRACTION", "0.34"))
OLLAMA_READY_WAIT_CAP_S     = float(os.getenv("OLLAMA_READY_WAIT_CAP_S", "45.0"))

# ── Mid-flight pause retry (added 2026-07-27) ─────────────────────────────────
# wait_then_budget() only covers a pause that's already active BEFORE the
# request starts. A pause that lands mid-request (governor trips while a
# generate() call is in flight) still hits a hard httpx timeout, because
# httpx's timeout is wall-clock and has no idea the far end is SIGSTOPped
# rather than just slow. Operator ask: when THAT happens, don't count the
# paused time against the request and fall back -- let it pick back up with
# a full fresh timeout once the pause releases.
#
# Ollama's /api/generate is non-streaming here ("stream": False) and there
# is no API-level checkpoint to resume a SIGSTOPped request's partial
# output from -- so "resume" in practice means: detect that the timeout
# was very likely pause-caused, wait (bounded) for the engine to answer a
# cheap health check again, then RE-ISSUE the same prompt with a brand new
# full-length timeout, rather than immediately handing the caller a
# deterministic/Anthropic fallback. This is an honest approximation of
# "reset the clock on resume," not a literal mid-generation resume -- flag
# this distinction if it matters for a given caller's cost/latency budget.
#
# Bounded on two axes so a genuinely broken (not just paused) engine can't
# retry forever: OLLAMA_MAX_RETRIES caps how many times a single generate()
# call will re-issue the prompt, OLLAMA_RETRY_WAIT_CAP_S caps how long each
# retry will wait for readiness before giving up (default matches the
# worst governor pause observed so far, ~19min, plus margin).
#
# "hot" priority (real-time VIP/TFR alert paths, see common/ollama_lock.py)
# NEVER enters this retry path -- a hot call must fail straight to fallback
# on any timeout, the same as before this change. Retrying through a
# possible 20-minute pause-wait is never acceptable for a call that exists
# specifically to never wait behind anything.
OLLAMA_MAX_RETRIES      = int(os.getenv("OLLAMA_MAX_RETRIES", "1"))
OLLAMA_RETRY_WAIT_CAP_S = float(os.getenv("OLLAMA_RETRY_WAIT_CAP_S", "1200.0"))


# ── Pre-flight thermal cool-launch gate (added 2026-07-27) ────────────────────
# Operator observation: the CPU only ever spikes into governor-trip range
# (75C+, see ollama_governor.py) DURING an active inference -- confirmed via
# thermal-ingest-guard's own logged samples, which show the box idling
# anywhere from the low 60s to low 70s C between inferences, then spiking to
# 79-83C during a generate() call before settling back down.
#
# wait_then_budget()/ollama_post_with_retry() above handle a pause that's
# ALREADY happened (governor SIGSTOPped mid-flight or beforehand). This is a
# different, earlier intervention: if the box is already running warm right
# before we're about to ADD inference heat on top of it, wait (bounded) for
# it to settle into a cooler starting band first, so the inference's own
# heat is less likely to cross the governor's trip point at all. Starting an
# inference from ~60C instead of ~72C buys meaningfully more thermal
# headroom before hitting 75C, for the exact same generation workload.
#
# Deliberately PASSIVE -- this does not itself stop/restart ingest
# containers. thermal-ingest-guard.py already does that reactively at
# 74C/79C, running as its own host-level systemd --user timer; duplicating
# that control from here would mean two independent systems mutating the
# same ingest container state from different processes (this runs inside
# the poller CONTAINER, which has no access to the host's systemctl --user
# session or scripts/ tree -- confirmed: only /sys/class/thermal is exposed
# into the container, not the host's systemd bus). If the box is genuinely
# hot enough to need active shedding, the existing guard is already handling
# that on its own 2-minute cadence; this wait just lets a report skill
# benefit from that cooldown-in-progress before firing instead of firing
# the instant Ollama merely isn't paused.
#
# Bounded and best-effort like every other wait in this module: if the
# target temp is never reached within the max wait, proceed anyway with
# whatever temp we've got. A scheduled brief must still eventually run --
# this is about improving the odds of a clean run, not blocking one.
#
# NOT applied to priority="hot" calls -- same rule as the pause-aware
# waits above. Also skipped when OLLAMA_BASE_URL is unset, since there's
# nothing local to protect if the call is going straight to Anthropic.
THERMAL_ZONE                  = "/sys/class/thermal/thermal_zone0/temp"
OLLAMA_PREFLIGHT_COOL_ENABLED  = os.getenv("OLLAMA_PREFLIGHT_COOL_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
OLLAMA_PREFLIGHT_COOL_TARGET_C = float(os.getenv("OLLAMA_PREFLIGHT_COOL_TARGET_C", "62.0"))
OLLAMA_PREFLIGHT_COOL_MAX_WAIT_S = float(os.getenv("OLLAMA_PREFLIGHT_COOL_MAX_WAIT_S", "240.0"))
OLLAMA_PREFLIGHT_COOL_POLL_S  = float(os.getenv("OLLAMA_PREFLIGHT_COOL_POLL_S", "15.0"))


def _read_cpu_temp_c() -> float | None:
    """Read the Pi's CPU temperature the same way ollama_governor.py and
    scripts/thermal-ingest-guard.py do. Returns None (never raises) if the
    sysfs path isn't readable -- e.g. a non-Pi dev environment -- so callers
    can treat "unknown" as "skip the gate" rather than crash a report run
    over a missing thermal zone."""
    try:
        with open(THERMAL_ZONE) as f:
            return float(f.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def wait_for_cool_launch(
    target_c: float = OLLAMA_PREFLIGHT_COOL_TARGET_C,
    max_wait_s: float = OLLAMA_PREFLIGHT_COOL_MAX_WAIT_S,
    poll_s: float = OLLAMA_PREFLIGHT_COOL_POLL_S,
) -> tuple[bool, float | None]:
    """Poll CPU temp until it's at/below target_c or max_wait_s elapses.
    Returns (reached_target, last_known_temp_c). last_known_temp_c is None
    only if the thermal zone was never readable at all (gate effectively a
    no-op in that environment).
    """
    temp = _read_cpu_temp_c()
    if temp is None:
        return True, None  # nothing to gate on -- proceed
    if temp <= target_c:
        return True, temp

    deadline = time.monotonic() + max_wait_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, temp
        time.sleep(min(poll_s, remaining))
        temp = _read_cpu_temp_c()
        if temp is None:
            return True, None
        if temp <= target_c:
            return True, temp


def preflight_cool_launch_if_needed(priority: str) -> None:
    """Called at the top of generate() for non-"hot" priority calls, before
    any Ollama readiness/generation work. Logs and blocks (bounded) if the
    box is running warm; no-ops immediately otherwise."""
    if priority == "hot" or not OLLAMA_PREFLIGHT_COOL_ENABLED or not OLLAMA_BASE_URL:
        return
    temp = _read_cpu_temp_c()
    if temp is None or temp <= OLLAMA_PREFLIGHT_COOL_TARGET_C:
        return
    log.info(
        "llm: pre-flight cool-launch gate -- %.1fC is above target %.1fC, "
        "waiting up to %.0fs before firing inference",
        temp, OLLAMA_PREFLIGHT_COOL_TARGET_C, OLLAMA_PREFLIGHT_COOL_MAX_WAIT_S,
    )
    reached, final_temp = wait_for_cool_launch(
        OLLAMA_PREFLIGHT_COOL_TARGET_C,
        OLLAMA_PREFLIGHT_COOL_MAX_WAIT_S,
        OLLAMA_PREFLIGHT_COOL_POLL_S,
    )
    if final_temp is None:
        return
    if reached:
        log.info("llm: pre-flight cool-launch -- reached %.1fC, proceeding", final_temp)
    else:
        log.warning(
            "llm: pre-flight cool-launch -- still %.1fC after %.0fs wait, "
            "proceeding anyway (never blocks a run indefinitely)",
            final_temp, OLLAMA_PREFLIGHT_COOL_MAX_WAIT_S,
        )


# ── Load-aware pre-flight gate (2026-08-09) ─────────────────────────────────
# Sibling of the thermal cool-launch gate above -- same shape, same rules
# (skipped for priority="hot" and when OLLAMA_BASE_URL is unset). Rationale:
# baseline load is ~5-6 on 4 cores (already oversubscribed); firing a ~5k-token
# CPU inference into a transient spike (a concurrent skill, a model build, an
# ingest burst) stacks contention and makes even small models blow the
# OLLAMA_TIMEOUT budget -- observed empirically 2026-08-09: at load ~15 every
# model, including 1.5B, timed out regardless of size. Rather than eat the
# timeout, hold until load returns near baseline, then fire. Best-effort:
# never blocks a run indefinitely.
OLLAMA_PREFLIGHT_LOAD_ENABLED    = os.getenv("OLLAMA_PREFLIGHT_LOAD_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
OLLAMA_PREFLIGHT_LOAD_TARGET     = float(os.getenv("OLLAMA_PREFLIGHT_LOAD_TARGET", "7.0"))
OLLAMA_PREFLIGHT_LOAD_MAX_WAIT_S = float(os.getenv("OLLAMA_PREFLIGHT_LOAD_MAX_WAIT_S", "180.0"))
OLLAMA_PREFLIGHT_LOAD_POLL_S     = float(os.getenv("OLLAMA_PREFLIGHT_LOAD_POLL_S", "15.0"))


def _read_loadavg1() -> float | None:
    """1-minute load average, or None (never raises) if unreadable -- so a
    non-Linux dev env treats 'unknown' as 'skip the gate' rather than crash."""
    try:
        with open("/proc/loadavg") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def wait_for_low_load(
    target: float = OLLAMA_PREFLIGHT_LOAD_TARGET,
    max_wait_s: float = OLLAMA_PREFLIGHT_LOAD_MAX_WAIT_S,
    poll_s: float = OLLAMA_PREFLIGHT_LOAD_POLL_S,
) -> tuple[bool, float | None]:
    """Poll 1-min load until at/below target or max_wait_s elapses. Returns
    (reached_target, last_known_load). last_known_load is None only if
    /proc/loadavg was never readable (gate a no-op in that environment)."""
    load = _read_loadavg1()
    if load is None:
        return True, None
    if load <= target:
        return True, load
    deadline = time.monotonic() + max_wait_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, load
        time.sleep(min(poll_s, remaining))
        load = _read_loadavg1()
        if load is None:
            return True, None
        if load <= target:
            return True, load


def preflight_load_gate_if_needed(priority: str) -> None:
    """Called in generate() right after the cool-launch gate, for non-"hot"
    calls. Holds (bounded) if 1-min load is above target so inference doesn't
    fire into contention and eat the timeout; no-ops immediately otherwise."""
    if priority == "hot" or not OLLAMA_PREFLIGHT_LOAD_ENABLED or not OLLAMA_BASE_URL:
        return
    load = _read_loadavg1()
    if load is None or load <= OLLAMA_PREFLIGHT_LOAD_TARGET:
        return
    log.info(
        "llm: pre-flight load gate -- load %.2f is above target %.2f, waiting "
        "up to %.0fs before firing inference",
        load, OLLAMA_PREFLIGHT_LOAD_TARGET, OLLAMA_PREFLIGHT_LOAD_MAX_WAIT_S,
    )
    reached, final_load = wait_for_low_load(
        OLLAMA_PREFLIGHT_LOAD_TARGET,
        OLLAMA_PREFLIGHT_LOAD_MAX_WAIT_S,
        OLLAMA_PREFLIGHT_LOAD_POLL_S,
    )
    if final_load is None:
        return
    if reached:
        log.info("llm: pre-flight load gate -- load %.2f, proceeding", final_load)
    else:
        log.warning(
            "llm: pre-flight load gate -- load still %.2f after %.0fs wait, "
            "proceeding anyway (never blocks a run indefinitely)",
            final_load, OLLAMA_PREFLIGHT_LOAD_MAX_WAIT_S,
        )


def _ollama_ready(timeout_s: float = OLLAMA_READY_TIMEOUT_S) -> bool:
    """Cheap health check against Ollama's own API. Never raises -- any
    exception (connection refused, read timeout, DNS failure, whatever)
    just means "not ready right now." This is what actually detects a
    governor thermal pause: a SIGSTOPped process accepts the TCP connection
    (backlog) but never completes the HTTP response, so this call reliably
    times out during a real pause and returns instantly when the engine is
    live.
    """
    if not OLLAMA_BASE_URL:
        return False
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=timeout_s)
        return resp.status_code == 200
    except Exception:
        return False


def wait_for_ollama_ready(max_wait_s: float, poll_interval_s: float = OLLAMA_READY_POLL_INTERVAL_S) -> bool:
    """Poll _ollama_ready() until it returns True or max_wait_s elapses.
    Returns True on the first check in the common case (engine not paused)
    at the cost of one _ollama_ready() call -- effectively free. Bounded
    so this never blocks longer than max_wait_s regardless of how it's
    called.
    """
    if max_wait_s <= 0:
        return _ollama_ready()
    deadline = time.monotonic() + max_wait_s
    while True:
        if _ollama_ready():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_interval_s, remaining))


def reserve_ready_wait_budget(effective_timeout: float) -> float:
    """How much of a caller's own timeout budget to spend polling for
    Ollama readiness before attempting the real generate call. Bounded by
    BOTH a fraction of that timeout and an absolute cap -- this wait is
    carved OUT of the caller's existing budget, never stacked on top of it.
    """
    return min(OLLAMA_READY_WAIT_CAP_S, effective_timeout * OLLAMA_READY_WAIT_FRACTION)


def wait_then_budget(effective_timeout: float) -> float | None:
    """Wait for Ollama readiness (bounded, carved out of effective_timeout),
    then return the timeout remaining for the actual generate/httpx call.

    Returns None if Ollama never became ready inside the bounded wait --
    the caller should skip straight to its own Anthropic/deterministic
    fallback rather than hand a still-paused engine a full-length generate
    call (which would just hang again until ITS timeout, paying the wait
    twice for nothing). This is the pre-flight half of pause-awareness --
    see ollama_post_with_retry() below for the mid-flight half (a pause
    that starts only after the request is already underway).
    """
    wait_budget = reserve_ready_wait_budget(effective_timeout)
    start = time.monotonic()
    ready = wait_for_ollama_ready(wait_budget)
    elapsed = time.monotonic() - start
    if not ready:
        return None
    return max(effective_timeout - elapsed, 5.0)


def ollama_post_with_retry(
    payload: dict,
    timeout: float,
    priority: str = "report",
    max_retries: int | None = None,
    retry_wait_cap: float | None = None,
) -> httpx.Response:
    """POST payload to Ollama's /api/generate, with mid-flight-pause retry.

    On a transport-level failure (read timeout, connect timeout, connection
    error -- anything httpx.TransportError covers) for a non-"hot" caller,
    this does NOT immediately give up. It checks/waits (bounded by
    retry_wait_cap) for Ollama to answer its own health check again, and if
    it does, re-issues the SAME request with a FRESH full-length timeout --
    effectively resetting the clock rather than counting the paused time
    against the original attempt. If the wait ceiling expires with the
    engine still unreachable, or max_retries is exhausted, the exception
    propagates to the caller same as before this change (caller's existing
    except OllamaBusyError / except Exception handling is unaffected).

    Each attempt acquires ollama_slot() fresh -- the slot is released
    while waiting for readiness between attempts, so a paused engine
    doesn't also block other "report"-priority callers from getting a
    turn once it resumes.

    priority="hot" never retries here -- raises immediately on the first
    transport failure, exactly like the pre-2026-07-27 behavior. A hot
    VIP/TFR alert path must never wait out a possible 20-minute pause.

    Added 2026-08-09: verifies the calling skill's source file (and, for a
    dedicated corporatetraveldc-pi5-* model, its Modelfile) against the
    signed manifest before ever reaching Ollama -- see
    _verify_before_inference() and docs/COMPLIANCE_SECURITY.md's "Signed
    Manifest Integrity". Raises IntegrityCheckFailed (not returned as a
    normal failure) if either doesn't match.
    """
    _verify_before_inference(payload.get("model"))

    retries = OLLAMA_MAX_RETRIES if max_retries is None else max_retries
    wait_cap = OLLAMA_RETRY_WAIT_CAP_S if retry_wait_cap is None else retry_wait_cap

    attempt = 0
    while True:
        try:
            with ollama_slot(priority=priority, timeout=timeout):
                resp = httpx.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json=payload,
                    timeout=timeout,
                )
            return resp
        except httpx.TransportError as exc:
            if priority == "hot" or attempt >= retries:
                raise
            attempt += 1
            log.info(
                "llm: Ollama request failed mid-flight (attempt %d/%d, %s) — "
                "checking for a governor pause before giving up",
                attempt, retries, exc,
            )
            if wait_for_ollama_ready(wait_cap):
                log.info(
                    "llm: Ollama reachable again after mid-flight pause — "
                    "retrying with a fresh %.0fs timeout (attempt %d/%d)",
                    timeout, attempt, retries,
                )
                continue
            log.warning(
                "llm: Ollama still unreachable after %.0fs retry-wait — giving up",
                wait_cap,
            )
            raise


def generate(
    system: str | None,
    prompt: str,
    ollama_model: str,
    max_tokens: int = 300,
    temperature: float = 0.2,
    priority: str = "report",
    timeout: float | None = None,
    allow_anthropic: bool = True,
    max_retries: int | None = None,
    retry_wait_cap: float | None = None,
) -> str | None:
    """
    Try Ollama, then Anthropic. Returns generated text or None if both fail.
    Callers should handle None with their own deterministic fallback.

    allow_anthropic (2026-08-06): False skips the Anthropic step entirely --
    Ollama failure/timeout goes straight to returning None, regardless of
    whether ANTHROPIC_API_KEY is set. Added for ops_brief.py, which the
    operator wants staying local-Ollama-or-deterministic only, never a
    cloud API call. Defaults to True so every other caller of generate()
    (route_impact, tfr_enrichment, osint_monitor, weekly_summary,
    aam_weekly_watch, dispatch_desk_memo, second_brain_daily/weekly,
    transport_pattern_digest) keeps today's Ollama-then-Anthropic behavior
    unchanged -- this is an opt-in restriction per caller, not a global
    removal of the fallback.

    A caller passing allow_anthropic=True (the default) can still be
    blocked by the separate module-level ANTHROPIC_FALLBACK_ENABLED env
    gate (2026-08-06) -- see the module docstring. That's the actual
    global off-switch for a whole deployment; allow_anthropic is a
    per-call opt-out on top of it, not instead of it. This box's
    dispatch.env sets ANTHROPIC_FALLBACK_ENABLED=false, so as of that
    change EVERY caller of generate() on this box is Ollama-or-None --
    ops_brief.py's own allow_anthropic=False is now redundant with the
    global gate (harmless to leave -- belt and suspenders, and it keeps
    working correctly if this box's env var is ever reverted without
    the code being touched).

    max_retries/retry_wait_cap (2026-08-06): pass through to
    ollama_post_with_retry() to override OLLAMA_MAX_RETRIES/
    OLLAMA_RETRY_WAIT_CAP_S per call. Added alongside allow_anthropic for
    ops_brief.py's fail-fast redesign -- see that module for why 0 retries
    is now correct there (a genuinely slow generate call was being retried
    with the identical slow prompt, guaranteeing the outer container
    timeout would kill the whole run before either attempt finished). None
    (default) keeps today's shared module-level defaults for every other
    caller.

    priority: "hot" for real-time VIP/TFR alert paths that must never wait
    behind a report job -- see common/ollama_lock.py. Defaults to "report"
    (the safe/conservative default: an unclassified caller defers to any
    pending hot work rather than risk starving a real-time alert). "hot"
    also disables both pause-handling paths below (pre-flight wait and
    mid-flight retry) -- a hot call fails straight to fallback on any
    delay or timeout, exactly like before 2026-07-27.

    timeout: overrides the shared OLLAMA_TIMEOUT (currently 60s, see
    dispatch.env) for both the lock-wait AND the actual generate() HTTP
    call. Added 2026-07-26 after the 60s value -- correctly tuned from real
    p99/max data for route_impact/tfr_enrichment/osint_monitor/
    weekly_summary/ops_brief's llm_generate path (all sub-minute) -- turned
    out to ALSO be silently applied to aam_weekly_watch.py,
    dispatch_desk_memo.py, second_brain_daily.py, and second_brain_weekly.py,
    whose legitimate generation times run 5-11 minutes (larger prompts:
    dispatch_desk_memo spans 90 items/6 categories vs. aam's 21/1). Those
    four calls were failing nearly every real run, silently falling back to
    boilerplate/raw-headline output with no visible error beyond a log line
    -- same failure shape the original 900s-default "stopgap" problem had,
    just inverted (too short instead of unbounded) and hitting different
    callers. Those four skills now pass their own explicit timeout at the
    call site (same pattern ep_advance_brief.py/ops_brief.py's own
    hardcoded-timeout direct calls already used, just via this shared
    function instead of a separate one). Unspecified (None) keeps today's
    shared-default behavior for the five original fast skills -- no
    behavior change for them.

    Added 2026-07-27: two layers of governor-pause awareness. (1) Before
    attempting Ollama at all, a bounded pre-flight readiness wait (see
    wait_then_budget()) so a pause already in progress is detected in ~3s
    instead of silently eating the whole timeout. (2) If a pause starts
    mid-request instead, the actual httpx call (inside _ollama() ->
    ollama_post_with_retry()) detects the resulting transport failure and
    retries with a fresh timeout once the engine answers again, bounded by
    OLLAMA_MAX_RETRIES / OLLAMA_RETRY_WAIT_CAP_S, rather than immediately
    falling back. Root cause for both: a real ~40-minute window
    (2026-07-27, ~07:56-08:38 EDT) where the thermal-ingest-guard was
    independently rendered inert by an unrelated bug AND every ops-brief
    run in a governor-pause window fell back to deterministic output with
    no earlier signal than "Ollama call timed out." Neither layer touches
    ollama_governor.py itself, which stays fully operator-only — see
    docs/SUDO_JUSTIFICATION_PROPOSAL.md.

    Added 2026-07-27: a pre-flight cool-launch gate runs before any of the
    above -- see preflight_cool_launch_if_needed()/wait_for_cool_launch().
    Bounded wait for the box to be at/below a target CPU temp before firing
    at all, so this inference's own heat is less likely to cross the
    governor's trip point on top of whatever the box was already running.
    Passive (does not itself touch ingest containers); "hot" priority skips
    it entirely, same as the pause-aware waits.
    """
    preflight_cool_launch_if_needed(priority)
    preflight_load_gate_if_needed(priority)
    # Both gates must be open -- see module docstring. Computed once so
    # the two branches below log identically regardless of which path
    # got here, and say WHICH gate is closed (useful for debugging a
    # box like this one where the global gate is off but individual
    # callers still pass allow_anthropic=True by default).
    anthropic_gate_open = allow_anthropic and ANTHROPIC_FALLBACK_ENABLED
    if allow_anthropic and not ANTHROPIC_FALLBACK_ENABLED:
        anthropic_blocked_reason = "ANTHROPIC_FALLBACK_ENABLED=false for this deployment"
    elif not allow_anthropic:
        anthropic_blocked_reason = "disabled for this caller (allow_anthropic=False)"
    else:
        anthropic_blocked_reason = None

    effective_timeout = OLLAMA_TIMEOUT if timeout is None else timeout
    if OLLAMA_BASE_URL:
        generate_timeout = wait_then_budget(effective_timeout) if priority != "hot" else effective_timeout
        if generate_timeout is None:
            log.info(
                "llm: Ollama not ready after bounded readiness wait "
                "(governor thermal pause?) — %s",
                f"Anthropic fallback {anthropic_blocked_reason}, returning None" if not anthropic_gate_open
                else "trying Anthropic fallback",
            )
        else:
            result = _ollama(system, prompt, ollama_model, max_tokens, temperature,
                              priority=priority, timeout=generate_timeout,
                              max_retries=max_retries, retry_wait_cap=retry_wait_cap)
            if result is not None:
                return result
            if not anthropic_gate_open:
                log.info("llm: Ollama unavailable, busy, or failed — Anthropic fallback %s, returning None", anthropic_blocked_reason)
                return None
            log.info("llm: Ollama unavailable, busy, or failed — trying Anthropic fallback")

    if anthropic_gate_open and ANTHROPIC_API_KEY:
        return _anthropic(system, prompt, max_tokens, temperature)

    return None


def _ollama(
    system: str | None,
    prompt: str,
    model: str,
    max_tokens: int,
    temperature: float,
    priority: str = "report",
    timeout: float = OLLAMA_TIMEOUT,
    max_retries: int | None = None,
    retry_wait_cap: float | None = None,
) -> str | None:
    payload = {
        "model":   model,
        "prompt":  prompt,
        "stream":  False,
        "options": {"num_predict": max_tokens, "temperature": temperature},
    }
    if system:
        # Non-empty system explicitly overrides the model's Modelfile
        # SYSTEM for this call. Omitting the key (system None/"") lets
        # Ollama use the dedicated model's own baked-in default instead.
        payload["system"] = system
    try:
        resp = ollama_post_with_retry(
            payload,
            timeout=timeout,
            priority=priority,
            max_retries=max_retries,
            retry_wait_cap=retry_wait_cap,
        )
        resp.raise_for_status()
        response_text = resp.json().get("response", "").strip()
        return response_text or None
    except OllamaBusyError as exc:
        log.info("llm: Ollama slot unavailable (priority=%s): %s", priority, exc)
        return None
    except Exception as exc:
        # 2026-08-07: was .debug -- every skill's main() sets
        # logging.basicConfig(level=logging.INFO), so this line was
        # silently invisible in every production log, and the caller's
        # own "Ollama unavailable, busy, or failed" line never says WHY.
        # Bumped to .warning to match the sibling "still unreachable"
        # line in ollama_post_with_retry() above -- this is a genuine
        # failure worth surfacing, not routine/expected behavior.
        log.warning("llm: Ollama call failed: %s", exc)
        return None


def _anthropic(
    system: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> str | None:
    try:
        import anthropic as _anthropic_sdk
        client = _anthropic_sdk.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=ANTHROPIC_FALLBACK_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip() if msg.content else ""
        return text or None
    except Exception as exc:
        log.warning("llm: Anthropic fallback failed: %s", exc)
        return None
