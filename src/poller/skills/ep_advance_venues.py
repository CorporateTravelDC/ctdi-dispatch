"""
ep-advance-venues — daily venue advisory half of the EP-Advance brief.

Split out of ep_advance_brief.py 2026-08-31 (operator directive) -- see
common/personas.py's 'ep-advance' persona comment for the full root-cause.
Short version: the venue matrix is ~70% of the old hourly prompt's tokens
and doesn't change hour-to-hour, but was being re-processed every hourly
fire on report-1's deliberately 2-thread-capped llama.cpp instance --
under real box contention, prompt processing alone never finished inside
the timeout. This skill carries that expensive half, runs once daily
(plus manual trigger: `python3 src/poller/skills/ep_advance_venues.py`),
and writes its output to state/ep-advance-venues.txt, where the hourly
ep_advance_brief.py splices it into the brief unchanged.

Schedule: once daily (corporatetraveldc-ep-advance-venues.timer).
SR-1: log_usage() in finally block.
SR-2: Exempt — time-bounded input; inputs always new.
"""

import logging
import pathlib
from datetime import datetime, timezone

from common.llm import generate as llm_generate
from common import config, ntfy_push as _ntfy
from common.sr1_log import log_usage

from poller.skills.ep_advance_brief import (
    OLLAMA_BASE_URL,
    _tfr_section,
    _weather_section,
    _nws_section,
    _route_section,
    _cps_section,
    _threat_sites_section,
    _osint_section,
    _venue_summary,
    _extended_venues_summary,
)

log = logging.getLogger(__name__)

SKILL_NAME   = "ep-advance-venues"
OLLAMA_MODEL = "corporatetraveldc-pi5-ep-advance-venues:latest"
MODEL        = OLLAMA_MODEL if OLLAMA_BASE_URL else "deterministic"
# Generous on purpose -- this runs once daily (plus manual trigger), never
# blocks the hourly brief, so a long worst-case wait costs nothing. Same
# prompt-size class as the old combined hourly call (~5850 tok), which was
# measured needing up to ~3600s under contention -- padded further since
# there's no cadence pressure here.
OLLAMA_TIMEOUT = 4200


def _call_ollama(prompt: str) -> str | None:
    if not OLLAMA_BASE_URL:
        return None
    narrative = llm_generate(
        system=None,
        prompt=prompt,
        ollama_model=OLLAMA_MODEL,
        max_tokens=900,
        temperature=0.15,
        top_p=0.9,
        timeout=OLLAMA_TIMEOUT,
        max_retries=0,
        allow_anthropic=False,
        priority="report",
    )
    return narrative.strip() if narrative else None


def _fallback_venues(venues: str, extended: str) -> str:
    now = datetime.now(timezone.utc).strftime("%b %d %H:%MZ")
    return (
        f"[EP-ADVANCE-VENUES FALLBACK — DETERMINISTIC] {now}\n"
        f"Ollama not configured or unavailable. Raw matrix only, no ranking.\n\n"
        f"{venues}\n\n{extended}"
    )


def main() -> None:
    status = "error"
    try:
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        tfr      = _tfr_section()
        weather  = _weather_section()
        nws      = _nws_section()
        route    = _route_section()
        cps      = _cps_section()
        threats  = _threat_sites_section()
        osint    = _osint_section()
        venues   = _venue_summary()
        extended = _extended_venues_summary()

        prompt = "\n\n".join([
            f"=== EP-ADVANCE-VENUES DATA PULL {now_utc} ===",
            f"TFR / SECURITY INDICATORS:\n{tfr}",
            f"WEATHER (DC AIRPORTS):\n{weather}",
            f"NWS ALERTS (DC/VA/MD):\n{nws}",
            f"CPS:\n{cps}",
            f"ROUTE / GROUND IMPACT:\n{route}",
            osint,
            threats,
            venues,
            extended,
        ])

        narrative = _call_ollama(prompt)
        if narrative:
            status = "ok"
            log.info("ep-advance-venues: generated via Ollama/%s", OLLAMA_MODEL)
        else:
            narrative = _fallback_venues(venues, extended)
            status = "ok"
            log.info("ep-advance-venues: generated (deterministic fallback)")

        state = pathlib.Path(config.state_dir())
        state.mkdir(parents=True, exist_ok=True)
        (state / "ep-advance-venues.txt").write_text(narrative)

        now_label = datetime.now(timezone.utc).strftime("%b %d %H:%MZ")
        title = f"EP-VENUES {now_label}"
        _ntfy.send("ep-advance", narrative, title=title, priority=3, tags="hotel")

    finally:
        log_usage(SKILL_NAME, MODEL, 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
