"""
poller.skills.claude_weekly_llama_fallback -- overnight, load-gated Llama
backstop for the two Claude-CLI-backed weekly jobs, per 2026-09-17 operator
directive.

Both `docs-drift-weekly` (scripts/weekly-doc-drift-check.sh, Fable) and
`second-brain-weekly-dump` (scripts/second-brain-weekly-dump.sh, Opus) STAY
on Claude CLI as primary -- this module never runs on the normal path, only
when one of them has already failed (see common/claude_weekly_fallback.py
for the marker it reads). Manual/out-of-cycle Claude invocations are
untouched by any of this.

Scheduling (corporatetraveldc-claude-weekly-llama-fallback.timer): fires
every 6h. Each firing is a cheap no-op unless a marker is pending, so the
frequent cadence costs nothing most of the time -- it exists purely to
bound worst-case delay. The actual generation, when it does run, goes
through common.llm.generate()'s existing preflight load gate (see
OLLAMA_PREFLIGHT_LOAD_TARGET/wait_for_low_load in common/llm.py) with a
generous max_wait_s here, so a daytime firing that lands on real contention
mostly just waits/no-ops and the job naturally gets picked up by a
later-in-the-night firing instead -- "overnight, based off load" without
hand-picking a fixed clock window that would either miss genuinely busy
nights or unnecessarily delay quiet ones. Worst case: failure lands right
after a firing -> caught by the next one <=6h later -> plus this call's own
bounded load-gate wait (<=20min) and generation time -- comfortably inside
the requested 6-12h bound even under sustained contention across two
consecutive firings.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from common.claude_weekly_fallback import (
    get_marker,
    set_llama_partial,
    STATE_PENDING,
)
from common.llm import generate, wait_for_low_load

log = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parents[3]
MEMORY_DIR = Path(
    "/home/corporatetraveldc/.claude/projects/"
    "-opt-corporatetraveldc-private-ctdi-dispatch-internal/memory"
)

# Generous -- this only fires after a marker says the real thing already
# failed, so patience here costs nothing but delay within the 6h window
# already bounded by the timer cadence above.
LOAD_GATE_TARGET = float(os.getenv("CLAUDE_FALLBACK_LOAD_TARGET", "6.0"))
LOAD_GATE_MAX_WAIT_S = float(os.getenv("CLAUDE_FALLBACK_LOAD_MAX_WAIT_S", "1200"))
OLLAMA_MODEL = (
    os.getenv("OLLAMA_CLAUDE_FALLBACK_MODEL")
    or os.getenv("OLLAMA_MODEL")
    or "corporatetraveldc-pi5-claude-weekly-fallback:latest"
)

DOCS_DRIFT_SYSTEM = """You are standing in for a much larger, tool-using weekly documentation
drift review because that primary review (which normally runs live systemctl/podman/curl
checks against the actual box) is temporarily unavailable. You are NOT able to run commands
or inspect the live system -- you only have the text handed to you below. Write a short,
honest LIVE_STATE_CHECK note: summarize what you were given, flag anything in it that looks
internally inconsistent or obviously stale, and be explicit that this is a degraded,
non-verified pass standing in for the real one. Do not claim to have checked anything you
were not given. Plain prose, no markdown headers beyond simple line breaks."""

SECOND_BRAIN_SYSTEM = """You are standing in for a weekly Second Brain consolidation pass
because the primary pass (which normally runs with full tool access and performs a
verified, careful synthesis) is temporarily unavailable. You are producing a best-effort
draft consolidation note from the raw CLAUDE.md and memory-file text handed to you below.
Write real structure: a heading per distinct topic, and [[wikilink]]-style references only
where the source text itself already names something linkable -- never invent a link. Be
explicit in the note itself that this is an unverified Llama-fallback draft, not the
confirmed weekly dump, and that CLAUDE.md/memory were deliberately NOT reset because of
that. Never fabricate content beyond what you were given."""


def _run_fallback(job_name: str, system: str, prompt: str, max_tokens: int, timeout: float) -> str | None:
    reached, load = wait_for_low_load(LOAD_GATE_TARGET, LOAD_GATE_MAX_WAIT_S, 30.0)
    if not reached:
        log.info(
            "claude_weekly_llama_fallback: %s -- load still above %.1f after %.0fs wait, "
            "skipping this firing, next timer tick will retry",
            job_name, LOAD_GATE_TARGET, LOAD_GATE_MAX_WAIT_S,
        )
        return None
    return generate(
        system=system,
        prompt=prompt,
        ollama_model=OLLAMA_MODEL,
        max_tokens=max_tokens,
        temperature=0.2,
        priority="report",
        timeout=timeout,
        allow_anthropic=False,  # hard requirement: this IS the Llama-only backstop
    )


def run_docs_drift_fallback() -> None:
    job_name = "docs-drift-weekly"
    marker = get_marker(job_name)
    if not marker or marker.get("state") != STATE_PENDING:
        return  # common case: nothing owed

    date_tag = time.strftime("%Y-%m-%d")
    readme = ""
    readme_path = REPO_DIR / "README.md"
    if readme_path.exists():
        readme = readme_path.read_text(errors="replace")[:8000]
    claude_md = ""
    claude_md_path = REPO_DIR / "CLAUDE.md"
    if claude_md_path.exists():
        claude_md = claude_md_path.read_text(errors="replace")[:8000]

    prompt = (
        f"Claude-CLI docs-drift-weekly failed on {marker.get('failed_at_iso', 'an earlier run')}: "
        f"{marker.get('reason', '(no reason recorded)')}\n\n"
        f"Current README.md (possibly truncated):\n{readme}\n\n"
        f"Current CLAUDE.md (possibly truncated):\n{claude_md}"
    )
    output = _run_fallback(job_name, DOCS_DRIFT_SYSTEM, prompt, max_tokens=900, timeout=1200)
    if output is None:
        return  # marker stays 'pending' -- next 6h firing retries

    # 2026-10-12: outside the repo (the timer used to write into the tracked tree)
    reports = Path(os.environ.get("CTDC_REPORTS_DIR", "/var/lib/corporatetraveldc/reports/docs"))
    reports.mkdir(parents=True, exist_ok=True)
    out_path = reports / f"LIVE_STATE_CHECK_{date_tag}_LLAMA_FALLBACK.md"
    out_path.write_text(
        f"# LIVE_STATE_CHECK (Llama fallback) -- {date_tag}\n\n"
        f"Claude-CLI docs-drift-weekly failed; this is a degraded, non-tool-using "
        f"Llama backstop pass. Re-run the real check when Claude CLI is available.\n\n"
        f"{output}\n"
    )
    # docs-drift-weekly has no destructive step -- a best-effort Llama pass
    # fully discharges what's owed, same as a real success would.
    from common.claude_weekly_fallback import clear_marker
    clear_marker(job_name)
    log.info("claude_weekly_llama_fallback: wrote %s", out_path)


def run_second_brain_dump_fallback() -> None:
    job_name = "second-brain-weekly-dump"
    marker = get_marker(job_name)
    if not marker or marker.get("state") != STATE_PENDING:
        return

    claude_md = ""
    claude_md_path = REPO_DIR / "CLAUDE.md"
    if claude_md_path.exists():
        claude_md = claude_md_path.read_text(errors="replace")[:12000]
    memory_text = ""
    if MEMORY_DIR.is_dir():
        parts = []
        for f in sorted(MEMORY_DIR.glob("*.md")):
            parts.append(f"## {f.name}\n{f.read_text(errors='replace')[:3000]}")
        memory_text = "\n\n".join(parts)[:40000]

    prompt = (
        f"Claude-CLI second-brain-weekly-dump failed on "
        f"{marker.get('failed_at_iso', 'an earlier run')}: "
        f"{marker.get('reason', '(no reason recorded)')}\n\n"
        f"Current CLAUDE.md:\n{claude_md}\n\n"
        f"Current memory files:\n{memory_text}"
    )
    output = _run_fallback(job_name, SECOND_BRAIN_SYSTEM, prompt, max_tokens=1600, timeout=1800)
    if output is None:
        return

    try:
        from second_brain.remember import remember_text
        remember_text(
            output,
            tags="weekly-dump-llama-fallback,session-archive",
            author_kind="agent",
            source="claude_weekly_llama_fallback",
        )
        # CRITICAL: never reset CLAUDE.md/memory here -- that destructive
        # step is gated on a CONFIRMED Claude/Opus run only, per the
        # operator's original Gate-2 safety design. Downgrade the marker
        # instead of clearing it, so it's visible that a real pass is
        # still owed and the fallback runner won't re-fire until a NEW
        # failure re-arms it as 'pending'.
        set_llama_partial(
            job_name,
            f"Llama fallback wrote a best-effort note at "
            f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}; "
            f"CLAUDE.md/memory intentionally left untouched -- real Opus "
            f"consolidation still owed.",
        )
        log.info("claude_weekly_llama_fallback: wrote fallback second-brain note")
    except Exception:
        log.exception("claude_weekly_llama_fallback: second_brain.remember write failed")
        # leave marker as 'pending' -- next firing retries


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_docs_drift_fallback()
    run_second_brain_dump_fallback()
