"""
common.llm — Shared LLM inference with Ollama-first / Anthropic fallback.

Priority:
  1. Ollama  (OLLAMA_BASE_URL set and reachable)
  2. Anthropic API  (ANTHROPIC_API_KEY set)
  3. None  (caller uses its own deterministic fallback)

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

import logging
import os
import time

import httpx

from common.ollama_lock import ollama_slot, OllamaBusyError

log = logging.getLogger(__name__)

OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "").rstrip("/")
OLLAMA_TIMEOUT    = int(os.getenv("OLLAMA_TIMEOUT", "900"))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Haiku is the Anthropic fallback — fast and cheap for short skill outputs.
ANTHROPIC_FALLBACK_MODEL = "claude-haiku-4-5-20251001"

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
    """
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
    system: str,
    prompt: str,
    ollama_model: str,
    max_tokens: int = 300,
    temperature: float = 0.2,
    priority: str = "report",
    timeout: float | None = None,
) -> str | None:
    """
    Try Ollama, then Anthropic. Returns generated text or None if both fail.
    Callers should handle None with their own deterministic fallback.

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
    """
    effective_timeout = OLLAMA_TIMEOUT if timeout is None else timeout
    if OLLAMA_BASE_URL:
        generate_timeout = wait_then_budget(effective_timeout) if priority != "hot" else effective_timeout
        if generate_timeout is None:
            log.info(
                "llm: Ollama not ready after bounded readiness wait "
                "(governor thermal pause?) — trying Anthropic fallback"
            )
        else:
            result = _ollama(system, prompt, ollama_model, max_tokens, temperature,
                              priority=priority, timeout=generate_timeout)
            if result is not None:
                return result
            log.info("llm: Ollama unavailable, busy, or failed — trying Anthropic fallback")

    if ANTHROPIC_API_KEY:
        return _anthropic(system, prompt, max_tokens, temperature)

    return None


def _ollama(
    system: str,
    prompt: str,
    model: str,
    max_tokens: int,
    temperature: float,
    priority: str = "report",
    timeout: float = OLLAMA_TIMEOUT,
) -> str | None:
    try:
        resp = ollama_post_with_retry(
            {
                "model":   model,
                "system":  system,
                "prompt":  prompt,
                "stream":  False,
                "options": {"num_predict": max_tokens, "temperature": temperature},
            },
            timeout=timeout,
            priority=priority,
        )
        resp.raise_for_status()
        response_text = resp.json().get("response", "").strip()
        return response_text or None
    except OllamaBusyError as exc:
        log.info("llm: Ollama slot unavailable (priority=%s): %s", priority, exc)
        return None
    except Exception as exc:
        log.debug("llm: Ollama call failed: %s", exc)
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
