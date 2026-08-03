"""
second_brain_weekly -- weekly "compile the wiki" step for the second-brain
vault. Reads the past 7 days of corporatetraveldc/01-Sources/daily/*.md
(written by second_brain_daily.py), synthesizes them into a weekly rollup,
and writes it to corporatetraveldc/04-Syntheses/weekly/.

Schedule: Sunday 18:15 ET (corporatetraveldc-second-brain-weekly.timer) --
15 minutes after the existing corporatetraveldc-weekly-summary.timer so
Ollama jobs don't stack.

This is the literal "compile my wiki" step from the original 2026-07-18
plan -- the single biggest gap flagged in docs/SECOND_BRAIN_STATUS.md
before this build (2026-07-22).
"""
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone

from common.llm import generate as llm_generate
from common.sr1_log import log_usage
from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
from second_brain.scrub_gate import ScrubGateBlocked, gate

log = logging.getLogger(__name__)

SKILL_NAME = "second-brain-weekly"
OLLAMA_MODEL = "corporatetraveldc-pi5-secondbrain-weekly:latest"

SYSTEM_PROMPT = """You are compiling a week's worth of daily operational logs
into one weekly synthesis for a second-brain knowledge vault. Identify
patterns across the days (recurring TFR types, weather trends, CPS
trajectory, notable watchlist activity) rather than just concatenating the
days.

Critical rules:
- The prompt's first line states the real week and date range being
  compiled. Do not invent, guess, or restate a different date or week
  anywhere in your output -- use only what the prompt gives you.
- Do not add a title, heading, or dateline of any kind (no "#", "##", or
  similar) -- the note this becomes already has its own week label in the
  surrounding document. Start directly with the first sentence of prose.
- Any number you cite (counts, delay minutes, etc.) must be quoted EXACTLY
  as it appears in the daily notes below -- do not round, abbreviate, or
  restate it differently.
- Under 500 words, plain prose paragraphs only."""


def _week_label(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def main() -> None:
    gate_result = "new"
    status = "error"
    today = date.today()

    try:
        daily_files = webdav_client.list_files(
            f"{webdav_client.BUSINESS_ROOT}/01-Sources/daily"
        )
        cutoff = today - timedelta(days=7)
        recent = [
            f for f in daily_files
            if f["path"].rsplit("/", 1)[-1][:10] >= cutoff.isoformat()
        ]
        recent.sort(key=lambda f: f["path"])

        if not recent:
            status = "no-content"
            log.info("%s: no daily notes in the past 7 days -- nothing to compile", SKILL_NAME)
            return

        bodies = []
        for f in recent:
            content = webdav_client.get(f["path"])
            if content:
                bodies.append(content.decode("utf-8", errors="replace"))

        week_range = f"{recent[0]['path'].rsplit('/', 1)[-1][:10]} through {recent[-1]['path'].rsplit('/', 1)[-1][:10]}"
        combined = (
            f"Week being compiled: {_week_label(today)} ({week_range})\n\n"
            + "\n\n---\n\n".join(bodies)
        )
        combined = gate(combined, source=SKILL_NAME)

        ollama_result = llm_generate(
            system=None, prompt=combined,  # dedicated Modelfile carries this now
            ollama_model=OLLAMA_MODEL, max_tokens=500, temperature=0.3,
            # Explicit timeout added 2026-07-26: reads up to 7 days of daily
            # notes, prompt size grows as the vault accumulates content, not
            # yet independently timed -- was silently inheriting the shared
            # OLLAMA_TIMEOUT=60s. 500s is conservative headroom under the
            # container's TimeoutStartSec=950 pending a real measurement
            # against a full 7-day dataset.
            timeout=500,
        )
        if ollama_result:
            ollama_result = gate(ollama_result, source=f"{SKILL_NAME}-llm")
            synthesis = ollama_result
            status = "ok"
            log.info("%s: synthesis generated via Ollama/%s", SKILL_NAME, OLLAMA_MODEL)
        else:
            synthesis = (
                "*This week's synthesis pass didn't complete in time (usually a "
                "thermal-governor pause on the inference box overlapping a long "
                "compile run) -- showing the week's daily notes directly below "
                "instead of a synthesized rollup.*\n\n" + combined[:3000]
            )
            status = "fallback"
            log.info("%s: Ollama unavailable -- using raw concatenation", SKILL_NAME)

        week_label = _week_label(today)
        frontmatter = (
            "---\n"
            f"week: {week_label}\n"
            "ingest_method: weekly-compile\n"
            f"generated_at: {datetime.now(timezone.utc).isoformat()}\n"
            f"source_notes: {len(recent)}\n"
            "---\n\n"
        )
        note = frontmatter + f"# Weekly Synthesis — {week_label}\n\n" + synthesis + "\n"

        rel_path = f"{webdav_client.BUSINESS_ROOT}/04-Syntheses/weekly/{week_label}.md"
        webdav_client.put(rel_path, note)
        log.info("%s: wrote %s from %d daily notes (status=%s)",
                  SKILL_NAME, rel_path, len(recent), status)

        conn = sqlite3.connect(INDEX_DB)
        init_vault_db(conn)
        index_note(
            conn, rel_path, title=f"Weekly Synthesis — {week_label}", content=note,
            tags="weekly,synthesis,auto", ingest_method="weekly-compile",
        )
        conn.close()

    except ScrubGateBlocked as e:
        status = "blocked"
        log.error("%s: BLOCKED by scrub gate: %s", SKILL_NAME, e)
    finally:
        log_usage(SKILL_NAME, OLLAMA_MODEL if status == "ok" else "deterministic",
                   0, 0, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
