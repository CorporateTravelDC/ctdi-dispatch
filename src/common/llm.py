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

import httpx

from common.ollama_lock import ollama_slot, OllamaBusyError

log = logging.getLogger(__name__)

OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "").rstrip("/")
OLLAMA_TIMEOUT    = int(os.getenv("OLLAMA_TIMEOUT", "900"))
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Haiku is the Anthropic fallback — fast and cheap for short skill outputs.
ANTHROPIC_FALLBACK_MODEL = "claude-haiku-4-5-20251001"


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
    pending hot work rather than risk starving a real-time alert).

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
    """
    effective_timeout = OLLAMA_TIMEOUT if timeout is None else timeout
    if OLLAMA_BASE_URL:
        result = _ollama(system, prompt, ollama_model, max_tokens, temperature,
                          priority=priority, timeout=effective_timeout)
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
        with ollama_slot(priority=priority, timeout=timeout):
            resp = httpx.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model":   model,
                    "system":  system,
                    "prompt":  prompt,
                    "stream":  False,
                    "options": {"num_predict": max_tokens, "temperature": temperature},
                },
                timeout=timeout,
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
