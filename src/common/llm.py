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
import json
import logging
import os
import pathlib
import re
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

    # 2026-08-13: as of the persona-consolidation refactor, the real
    # persona/discipline/ROE content lives in corporatetraveldc.dispatch-
    # persona, not in the (now near-empty) Modelfiles -- verify it here too,
    # or an attacker/accidental edit to the one file that actually carries
    # every skill's instructions would bypass the integrity gate entirely.
    _verify_integrity(_PERSONA_PATH.name)

log = logging.getLogger(__name__)

# 2026-08-13: single shared system prompt, replacing 16 separate Modelfiles'
# baked-in SYSTEM blocks. Modelfiles now carry ONLY FROM + PARAMETER --
# zero persona content -- so every model can share one resident base
# without a swap cycle every time a different skill fires. Individual
# skill .py files carry no persona content either; every call's system=
# stays None (unchanged -- no call-site edits needed), and the actual
# persona gets injected once, centrally, in ollama_post_with_retry() below
# (the true convergence point for every call path, including
# ep_advance_brief.py's direct calls that bypass generate()/_ollama()
# entirely -- see that function's own docstring).
#
# Private file (corporatetraveldc.dispatch-persona, real firm/operator
# content) is excluded from the public mirror -- see scrub-public-tree.py
# DROP_FILES. Public gets corporatetraveldc.dispatch-persona.template only.
# Loaded once at import time and cached; a missing file logs loudly and
# falls back to empty (degrades to no persona at all, not a crash -- see
# _load_dispatch_persona()'s own docstring for why that's the right
# failure mode here).
_PERSONA_PATH = _REPO_ROOT / "corporatetraveldc.dispatch-persona"


def _load_dispatch_persona() -> str:
    """Read the shared persona file once at import time.

    Soft-fails to "" rather than raising: a missing persona file means
    every skill's output silently loses its identity/ROE grounding, which
    is a real quality problem but not one that should crash the whole
    poller process over -- the degraded (persona-less) output itself is
    the visible symptom, logged loudly here so it's not a silent regression.
    """
    try:
        lines = _PERSONA_PATH.read_text(encoding="utf-8").splitlines()
        first = 0
        while first < len(lines) and (
            not lines[first].strip() or lines[first].lstrip().startswith("#")
        ):
            first += 1
        return "\n".join(lines[first:]).strip()
    except FileNotFoundError:
        log.error(
            "llm: shared persona file missing (%s) -- every skill will "
            "generate without persona/ROE grounding until this is fixed. "
            "See corporatetraveldc.dispatch-persona.template to rebuild it.",
            _PERSONA_PATH,
        )
        return ""


DISPATCH_PERSONA: str = _load_dispatch_persona()

# 2026-08-13: DISPATCH_PERSONA itself measured at ~2050 real tokens (16466
# chars) via a live prompt_eval_count check against corporatetraveldc-pi5-
# brief -- sent on every single call now that persona is centralized, so it
# eats a fixed, non-negotiable chunk of whatever num_ctx the shared model
# is built with (4096 by default -> see corporatetraveldc.brief). Callers
# with a large/unbounded raw-data prompt (dispatch_desk_memo.py's 90-item/
# 6-category feed, second_brain_weekly.py's up-to-7-days of vault notes)
# need to budget their own prompt against what's actually left, not what
# num_ctx nominally offers. ~4 chars/token is a conservative average for
# this kind of headline/prose English text (real content tends to run
# closer to 4.5-5.5); undercounting the budget (truncating a bit more than
# strictly necessary) is the safe direction to be wrong in here.
_CHARS_PER_TOKEN_ESTIMATE = 4.0


def trim_to_token_budget(text: str, max_tokens: int) -> str:
    """Truncate text to roughly max_tokens, cutting on a line boundary
    where possible so we drop whole headlines/entries rather than mid-word.
    A cheap chars-per-token heuristic, not a real tokenizer -- exact enough
    for keeping a prompt inside a fixed num_ctx budget, not for precision
    token accounting."""
    max_chars = int(max_tokens * _CHARS_PER_TOKEN_ESTIMATE)
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_break = truncated.rfind("\n")
    if last_break > max_chars * 0.5:  # don't chop a huge tail for a stray early newline
        truncated = truncated[:last_break]
    return truncated.rstrip() + "\n[... truncated to fit the model's context budget ...]"


# ── Prompt content sanitization (2026-08-13) ────────────────────────────────
# Operator directive: guard against a prompt padded with injected shell/code
# content -- e.g. via a compromised or malformed RSS feed item, or a
# corrupted vault note -- that either inflates compute cost well beyond what
# real operational data would ("extra load... extra defeat on the box"), or
# gets echoed verbatim into a brief's output and from there into an ntfy
# push notification or a rendered view. This is a lightweight defense-in-
# depth heuristic targeting the actual injection surface here (headline/
# title/note text assembled into a prompt) -- not a general-purpose WAF.
_SUSPICIOUS_PROMPT_RES = [
    re.compile(r"#!\s*/(bin|usr)/"),               # shebang
    re.compile(r"`[^`\n]{0,200}`"),                 # backtick command substitution
    re.compile(r"\$\([^)\n]{0,200}\)"),             # $(...) command substitution
    re.compile(r"<script[\s>]", re.IGNORECASE),     # embedded script tag
    re.compile(r"\b(eval|exec)\s*\("),              # eval(/exec( call patterns
    re.compile(r"\brm\s+-rf\b"),
]
# A real headline/title/note never has an unbroken "word" this long --
# injected/obfuscated payloads (base64 blobs, minified code, stuffed URLs)
# typically do.
_MAX_SINGLE_TOKEN_CHARS = 300


def sanitize_prompt_text(text: str, source: str = "prompt") -> str:
    """Best-effort content hygiene pass on prompt text assembled from
    external sources before it reaches Ollama: strips non-printable control
    characters, redacts segments matching known code/shell-injection
    markers, and collapses any single unbroken run of 300+ non-whitespace
    characters. Logs a warning (with before/after length, not the raw
    content) only when it actually changes something -- silent on clean
    input, which is the overwhelming common case."""
    if not text:
        return text
    original_len = len(text)
    cleaned = "".join(ch for ch in text if ch in "\n\t" or ch.isprintable())
    for pattern in _SUSPICIOUS_PROMPT_RES:
        cleaned = pattern.sub("[redacted -- suspicious pattern]", cleaned)
    cleaned = re.sub(
        rf"\S{{{_MAX_SINGLE_TOKEN_CHARS},}}",
        lambda m: m.group(0)[:_MAX_SINGLE_TOKEN_CHARS] + "[...truncated long token...]",
        cleaned,
    )
    if cleaned != text:
        log.warning(
            "llm: %s sanitized (%d -> %d chars) -- suspicious content redacted/"
            "truncated. Worth checking the source feed/note if this recurs.",
            source, original_len, len(cleaned),
        )
    return cleaned


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


# ── Ollama-priority ingest backpressure (2026-08-11) ────────────────────────
# Active complement to the passive load gate above: that gate only WAITS for
# load to drop on its own, which never happens if ingest's steady-state CPU
# draw IS the contention rather than a transient spike -- confirmed live
# 2026-08-11 when a cold model load lost the CPU race entirely under normal
# (non-thermal) ingest load, see docs/benchmarks/
# OLLAMA_BACKPRESSURE_AB_2026-08-11.md. This engages the same
# bandwidth_priority backpressure valve weather events use (common/db.py,
# built 2026-07-26) for the duration of the Ollama call -- pauses the
# low-priority SWIM feeds (stdds/tbfm/itws/fns), leaves fdps/tfms alone. Soft
# pause only (stops draining the queue, stays connected) -- no container
# stop/restart, no backlog-fast-forward-triage; the accepted tradeoff is a
# burst of queued messages/notifications once released. Skipped for
# priority="hot", same as every other gate here. Disabled by default until
# validated -- flip on per-deployment via dispatch.env.
OLLAMA_BACKPRESSURE_ENABLED = os.getenv("OLLAMA_BACKPRESSURE_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
OLLAMA_BACKPRESSURE_TTL_S   = float(os.getenv("OLLAMA_BACKPRESSURE_TTL_S", "600.0"))


def _engage_ollama_backpressure(priority: str) -> bool:
    """Best-effort: set bandwidth_priority=ollama so ingest's low-priority
    SWIM feeds stop draining their queue for the duration of this call.
    Returns True if this call actually engaged it (so the caller knows to
    release it) -- False if disabled, skipped for "hot", or something else
    (operator, a real weather event) already has priority engaged. Never
    raises, never blocks."""
    if priority == "hot" or not OLLAMA_BACKPRESSURE_ENABLED:
        return False
    try:
        from common import db as _db
        state = _db.get_bandwidth_priority()
        if state.get("active") and state.get("set_by") not in (None, "auto", "auto-ollama"):
            return False
        _db.set_bandwidth_priority(
            "ollama", set_by="auto-ollama",
            reason="Ollama inference in flight", ttl_seconds=OLLAMA_BACKPRESSURE_TTL_S,
        )
        return True
    except Exception as exc:
        log.info("llm: failed to engage ingest backpressure (non-fatal): %s", exc)
        return False


def _release_ollama_backpressure() -> None:
    """Clear bandwidth_priority back to auto after a call this module
    engaged it for. Only clears if still set_by="auto-ollama" -- if an
    operator or a real weather event took it over in the meantime, leave it
    alone rather than clobbering their state."""
    try:
        from common import db as _db
        state = _db.get_bandwidth_priority()
        if state.get("set_by") == "auto-ollama":
            _db.set_bandwidth_priority("auto", set_by="auto-ollama", reason="Ollama call finished")
    except Exception as exc:
        log.info("llm: failed to release ingest backpressure (non-fatal): %s", exc)


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


def _abandon_ollama_generation(model: str | None) -> None:
    """Best-effort: tell Ollama to unload `model` the moment a caller's own
    request times out, rather than leaving the underlying llama-server
    child to keep computing for an abandoned client.

    Found 2026-08-10/11: a timed-out httpx call releases ollama_slot()
    (the caller's process exits/moves to fallback), but that does NOT stop
    the actual generation running server-side inside Ollama's own
    llama-server child -- it keeps burning CPU/RAM for a client that's
    already gone, sometimes for 15-20+ minutes. Because ollama_slot() is a
    CLIENT-side lock, its release does not mean the resource it was meant
    to protect is actually free -- the next caller (hot or report) can
    acquire the now-open slot immediately and fire straight into
    contention with this orphaned generation, which is exactly how one
    real timeout cascades into several. Confirmed live: a fresh reboot
    (14min uptime, no prior test load) already reproduced this with a real
    ep-advance run whose llama-server child kept running at 200%+ CPU
    3+ minutes after ep-advance.service itself had exited.

    Deliberately NOT the `ollama` CLI -- that binary lives on the host,
    not inside the poller/skill containers this module runs in. Uses the
    documented HTTP-only unload signal instead (empty prompt +
    keep_alive=0). Best-effort and non-fatal by design: if this fails, the
    caller's existing fallback behavior is completely unaffected -- this
    only ever improves the odds for whichever request comes next.
    """
    if not model or not OLLAMA_BASE_URL:
        return
    try:
        httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": 0},
            timeout=5.0,
        )
        log.info("llm: sent unload signal for %s after timeout, so the next caller doesn't inherit it", model)
    except Exception as exc:
        log.info("llm: best-effort unload signal for %s failed (non-fatal): %s", model, exc)


# ── Load-phase timeout, separate from generation time (2026-08-13) ─────────
# Operator directive after tonight's diagnostic arc: the ONLY thing worth
# time-boxing is whether the MODEL LOADS promptly. Once it's loaded,
# generation can legitimately take 5-15+ minutes for a long-form brief --
# that's an accepted cost, not a failure, as long as it eventually produces
# real output. What actually matters is (1) load doesn't stall indefinitely
# and (2) a model that keeps getting evicted and reloaded doesn't thrash the
# box. Backpressure (see OLLAMA_BACKPRESSURE_ENABLED above) is what gives
# the load phase its best shot at the CPU -- this timeout is the bounded
# backstop if that's not enough.
OLLAMA_LOAD_TIMEOUT = float(os.getenv("OLLAMA_LOAD_TIMEOUT", "180.0"))

# ── Adaptive load-time baseline (2026-08-13, later same day) ───────────────
# Refined per operator directive: a fixed guess (the static OLLAMA_LOAD_TIMEOUT
# above) isn't as sharp a guard as learning what NORMAL load time actually
# looks like on THIS box and gating on deviation from that -- 125% of the
# rolling mean over the last 48h. This is deliberately an anomaly/tamper
# signal, not just a performance guard: "somebody can't ingest something,
# even when they properly sign a manifest or reload a second model file at
# the same time" -- an unusually slow load right after a manifest sign or
# model rebuild is exactly the kind of otherwise-legitimate-looking event
# this should catch and flag loudly, even if it still technically completes.
# OLLAMA_LOAD_TIMEOUT remains the fallback used until enough history exists
# to trust a computed baseline (OLLAMA_LOAD_BASELINE_MIN_SAMPLES).
_LOAD_HISTORY_PATH = pathlib.Path("/var/lib/corporatetraveldc/ollama-lock/load-duration-history.json")
OLLAMA_LOAD_BASELINE_WINDOW_S    = float(os.getenv("OLLAMA_LOAD_BASELINE_WINDOW_S", str(48 * 3600)))
OLLAMA_LOAD_BASELINE_MULTIPLIER  = float(os.getenv("OLLAMA_LOAD_BASELINE_MULTIPLIER", "1.25"))
OLLAMA_LOAD_BASELINE_MIN_SAMPLES = int(os.getenv("OLLAMA_LOAD_BASELINE_MIN_SAMPLES", "5"))
# Even at 125% of a suspiciously fast rolling mean, never bound the load
# phase tighter than this -- real cold loads measured live tonight ran
# ~30-80s; this leaves room for a fluke fast sample without the guard
# becoming self-defeating.
OLLAMA_LOAD_BASELINE_FLOOR_S = float(os.getenv("OLLAMA_LOAD_BASELINE_FLOOR_S", "90.0"))


def _load_history() -> list[dict]:
    if not _LOAD_HISTORY_PATH.exists():
        return []
    try:
        return json.loads(_LOAD_HISTORY_PATH.read_text())
    except Exception:
        return []


def _record_load_duration_sample(load_s: float) -> None:
    """Append a real cold-load duration to the rolling baseline history,
    pruning anything outside OLLAMA_LOAD_BASELINE_WINDOW_S. Best-effort --
    never raises (a guardrail that can crash the thing it's guarding is
    worse than no guardrail)."""
    try:
        _LOAD_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        samples = [
            s for s in _load_history()
            if isinstance(s, dict) and now - s.get("ts", 0) < OLLAMA_LOAD_BASELINE_WINDOW_S
        ]
        samples.append({"ts": now, "load_s": load_s})
        _LOAD_HISTORY_PATH.write_text(json.dumps(samples))
    except Exception as exc:
        log.info("llm: failed to record load-duration sample (non-fatal): %s", exc)


def _adaptive_load_timeout() -> tuple[float, str]:
    """(timeout_s, human-readable basis) -- OLLAMA_LOAD_BASELINE_MULTIPLIER
    (125%) of the rolling mean load time over the last
    OLLAMA_LOAD_BASELINE_WINDOW_S if enough samples exist, floored at
    OLLAMA_LOAD_BASELINE_FLOOR_S; falls back to the static OLLAMA_LOAD_TIMEOUT
    if there isn't yet enough history to trust a computed baseline."""
    now = time.time()
    samples = [
        s.get("load_s") for s in _load_history()
        if isinstance(s, dict) and now - s.get("ts", 0) < OLLAMA_LOAD_BASELINE_WINDOW_S
        and isinstance(s.get("load_s"), (int, float))
    ]
    if len(samples) < OLLAMA_LOAD_BASELINE_MIN_SAMPLES:
        return (
            OLLAMA_LOAD_TIMEOUT,
            f"static fallback ({len(samples)}/{OLLAMA_LOAD_BASELINE_MIN_SAMPLES} baseline samples so far)",
        )
    mean_s = sum(samples) / len(samples)
    threshold = max(OLLAMA_LOAD_BASELINE_FLOOR_S, mean_s * OLLAMA_LOAD_BASELINE_MULTIPLIER)
    return (
        threshold,
        f"{OLLAMA_LOAD_BASELINE_MULTIPLIER:.0%} of {len(samples)}-sample "
        f"{OLLAMA_LOAD_BASELINE_WINDOW_S / 3600:.0f}h rolling mean ({mean_s:.1f}s)",
    )


# Reload-storm guardrail: file-backed (not in-process -- every skill run is
# its own short-lived subprocess, see ollama_lock.py's own module docstring
# for why cross-process state here has to be a file, not a Python global)
# record of recent COLD loads (load_duration > 1s, i.e. the model actually
# had to load, not an already-warm no-op). If the same box needs more than
# OLLAMA_RELOAD_GUARD_MAX real loads inside OLLAMA_RELOAD_GUARD_WINDOW_S,
# something is evicting the model repeatedly (OOM, another model competing
# for OLLAMA_MAX_LOADED_MODELS, ollama.service flapping) -- piling on
# another load attempt just makes it worse, so this trips a cool-down
# (skip Ollama, straight to fallback) instead.
_RELOAD_GUARD_PATH = pathlib.Path("/var/lib/corporatetraveldc/ollama-lock/recent-cold-loads.json")
OLLAMA_RELOAD_GUARD_MAX       = int(os.getenv("OLLAMA_RELOAD_GUARD_MAX", "4"))
OLLAMA_RELOAD_GUARD_WINDOW_S  = float(os.getenv("OLLAMA_RELOAD_GUARD_WINDOW_S", "900.0"))


def _record_cold_load_and_check_storm(model: str) -> bool:
    """Record a real cold load for `model`, prune anything outside the
    guard window, and return True if the count within that window has hit
    OLLAMA_RELOAD_GUARD_MAX. Never raises -- a guardrail that can crash the
    thing it's guarding is worse than no guardrail (fails open: on any
    error, just don't trip)."""
    try:
        _RELOAD_GUARD_PATH.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        events: list = []
        if _RELOAD_GUARD_PATH.exists():
            try:
                events = json.loads(_RELOAD_GUARD_PATH.read_text())
            except Exception:
                events = []
        events = [t for t in events if isinstance(t, (int, float)) and now - t < OLLAMA_RELOAD_GUARD_WINDOW_S]
        events.append(now)
        _RELOAD_GUARD_PATH.write_text(json.dumps(events))
        if len(events) >= OLLAMA_RELOAD_GUARD_MAX:
            log.error(
                "llm: RELOAD-STORM GUARD TRIPPED -- %s has cold-loaded %d times "
                "in the last %.0fs. Something is evicting it repeatedly -- "
                "investigate (OOM? OLLAMA_MAX_LOADED_MODELS contention? "
                "ollama.service restarting?) rather than letting this clear on "
                "its own. Skipping Ollama for this call.",
                model, len(events), OLLAMA_RELOAD_GUARD_WINDOW_S,
            )
            return True
        return False
    except Exception as exc:
        log.info("llm: reload-storm guard check failed (non-fatal, fails open): %s", exc)
        return False


def _preload_model(model: str | None, priority: str) -> None:
    """Bounded, backpressure-assisted probe that the model is loaded,
    BEFORE the real generate call -- separates "did it load" (time-boxed by
    the adaptive baseline, see _adaptive_load_timeout()) from "how long did
    it generate" (no longer tightly bounded, see this section's own comment
    above). Sends the same empty-prompt technique _abandon_ollama_generation()
    uses to unload, inverted to load instead.

    Raises httpx.TransportError on a real timeout, or RuntimeError if the
    reload-storm guard has tripped -- both propagate through
    ollama_post_with_retry() exactly like any other Ollama-unavailable
    condition, so no new caller-side exception handling is needed anywhere.

    Skipped for priority="hot" (and if OLLAMA_BASE_URL isn't set) -- a
    real-time alert can't afford an extra bounded wait on top of its own
    timeout; hot calls go straight to the real generate call, unchanged."""
    if priority == "hot" or not OLLAMA_BASE_URL or not model:
        return
    load_budget, basis = _adaptive_load_timeout()
    backpressure_engaged = _engage_ollama_backpressure(priority)
    try:
        with ollama_slot(priority=priority, timeout=load_budget):
            resp = httpx.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": model, "prompt": ""},
                timeout=load_budget,
            )
        resp.raise_for_status()
        load_s = (resp.json().get("load_duration") or 0) / 1e9
        if load_s > 1.0:
            log.info("llm: %s cold-loaded in %.1fs (budget %.0fs, basis: %s; backpressure %s)",
                      model, load_s, load_budget, basis,
                      "engaged" if backpressure_engaged else "not engaged")
            if _record_cold_load_and_check_storm(model):
                raise RuntimeError(f"reload-storm guard tripped for {model}")
            # Flag even a SUCCESSFUL load that's abnormally slow vs its own
            # baseline before folding it into that same baseline -- this is
            # the tamper/anomaly signal the guardrail exists for. A load
            # that barely made it under load_budget but is 2-3x the normal
            # baseline is worth a loud line in the logs even though nothing
            # here failed.
            if load_budget > OLLAMA_LOAD_BASELINE_FLOOR_S and load_s > load_budget * 0.8:
                log.warning(
                    "llm: %s took %.1fs to load -- within budget (%s) but "
                    "close to it. If a manifest was just signed or a model "
                    "file rebuilt, worth confirming it wasn't tampered with "
                    "or accidentally swapped for something larger.",
                    model, load_s, basis,
                )
            _record_load_duration_sample(load_s)
    finally:
        if backpressure_engaged:
            _release_ollama_backpressure()


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

    # 2026-08-13: inject the shared persona if the caller didn't set one.
    # Every call path converges here (see DISPATCH_PERSONA's own comment
    # above), so this single line is what makes the Modelfile-stripping
    # refactor actually take effect for every skill, including
    # ep_advance_brief.py's direct calls that build their own payload dict
    # and never pass a "system" key at all.
    if not payload.get("system"):
        payload["system"] = DISPATCH_PERSONA

    # 2026-08-13: sanitize the prompt content itself -- see
    # sanitize_prompt_text()'s own docstring. Applied here, not per-skill,
    # so every call path gets it with no per-skill changes needed (same
    # convergence-point pattern as the persona injection just above).
    # system= is our own trusted persona file, not externally-influenced
    # content, so it's deliberately left alone.
    if payload.get("prompt"):
        payload["prompt"] = sanitize_prompt_text(payload["prompt"], source=f"prompt to {payload.get('model')}")

    # 2026-08-13: bound the LOAD phase specifically, separate from
    # generation time -- see OLLAMA_LOAD_TIMEOUT's own comment above. On
    # success this is a near-no-op (model already resident, load_duration
    # ~0). On failure it raises (httpx.TransportError or RuntimeError for a
    # tripped reload-storm guard) straight out of this function -- every
    # caller (generate()'s _ollama(), ep_advance_brief.py's direct calls)
    # already has a catch-all except Exception around this call, so that's
    # treated exactly like "Ollama unavailable" with no new handling needed.
    _preload_model(payload.get("model"), priority)

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
            _abandon_ollama_generation(payload.get("model"))
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
        # Backpressure engaged BEFORE the load gate's own wait (not just
        # around the final generate call) so that wait period actually
        # benefits from ingest backing off, rather than passively hoping
        # load drops on its own -- see OLLAMA_BACKPRESSURE_ENABLED above
        # for why the passive-only gate wasn't enough. Released once this
        # whole Ollama attempt (gate wait + generate call) is done,
        # success or failure.
        backpressure_engaged = _engage_ollama_backpressure(priority)
        ollama_attempted = False
        try:
            preflight_load_gate_if_needed(priority)
            generate_timeout = wait_then_budget(effective_timeout) if priority != "hot" else effective_timeout
            if generate_timeout is None:
                log.info(
                    "llm: Ollama not ready after bounded readiness wait "
                    "(governor thermal pause?) — %s",
                    f"Anthropic fallback {anthropic_blocked_reason}, returning None" if not anthropic_gate_open
                    else "trying Anthropic fallback",
                )
            else:
                ollama_attempted = True
                result = _ollama(system, prompt, ollama_model, max_tokens, temperature,
                                  priority=priority, timeout=generate_timeout,
                                  max_retries=max_retries, retry_wait_cap=retry_wait_cap)
                if result is not None:
                    return result
        finally:
            if backpressure_engaged:
                _release_ollama_backpressure()

        if ollama_attempted:
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
        # Non-empty system is an explicit per-call override. Omitting the
        # key (system None/"") is the normal case for every skill as of
        # 2026-08-13 -- ollama_post_with_retry() fills in the shared
        # DISPATCH_PERSONA downstream (Modelfiles no longer carry any
        # persona of their own to fall back to).
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
