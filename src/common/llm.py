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
        ollama_model="csexec-osint:latest",
        max_tokens=200,
        temperature=0.2,
    )
    if text is None:
        text = deterministic_fallback(...)
"""

import logging
import os

import httpx

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
) -> str | None:
    """
    Try Ollama, then Anthropic. Returns generated text or None if both fail.
    Callers should handle None with their own deterministic fallback.
    """
    if OLLAMA_BASE_URL:
        result = _ollama(system, prompt, ollama_model, max_tokens, temperature)
        if result is not None:
            return result
        log.info("llm: Ollama unavailable or failed — trying Anthropic fallback")

    if ANTHROPIC_API_KEY:
        return _anthropic(system, prompt, max_tokens, temperature)

    return None


def _ollama(
    system: str,
    prompt: str,
    model: str,
    max_tokens: int,
    temperature: float,
) -> str | None:
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model":   model,
                "system":  system,
                "prompt":  prompt,
                "stream":  False,
                "options": {"num_predict": max_tokens, "temperature": temperature},
            },
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()
        return text or None
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
