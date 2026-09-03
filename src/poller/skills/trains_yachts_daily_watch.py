"""
trains_yachts_daily_watch -- daily rail/marine industry watch. Same
architecture as gig_economy_daily_watch.py/concierge_travel_daily_watch.py,
added 2026-08-07 per operator request: shipbuilder output shifts, new
shipyard openings, and next-gen rail program status (e.g. Acela rebuild)
are exactly the kind of first-mover/ahead-of-the-curve signal this
pipeline is built to catch, not just static scheduled tracking.

Combined rail + marine into one category rather than splitting -- total
feed volume for either half alone is thin (matches gig_economy's own
precedent of starting small).

Reuses aam_weekly_watch.py's _fetch_week_items(). No longer reuses
_split_framings() (see 2026-09-02 note below). Wired into
common.entity_tracking from creation, same pattern as every other
daily-watch skill built 2026-08-06/07.

2026-09-02 (operator-directed rearchitecture, same pattern applied
across the whole daily-watch family -- see aam_daily_watch.py's
docstring for the full root-cause writeup): ops and EP framings are now
TWO INDEPENDENT llm_generate() calls joined afterward, not one shared
500-token call mechanically split via _split_framings(). A live
usage-log audit found this family falling back at 10-17%, the same
mechanism root-caused on aam-daily-watch (83% there): the single shared
call instructs the model to write two versions back to back, which on a
quiet news day collapse into near-duplicate text and loop past the
token cap, tripping common.llm's repetition-loop guard into discarding
the whole response. Per-framing calls remove the instructed
duplication and isolate failures.

No ntfy push for the brief itself -- same established convention.

Schedule: daily 08:30 ET (corporatetraveldc-trains-yachts-daily-watch.timer)
-- after concierge-travel (08:15), clear of the hourly ops-brief/
ep-advance :00/:30 marks (prewarm at 08:27).

Output:
  1. trains_yachts_daily_watch_ops.txt / _ep.txt in state_dir().
  2. corporatetraveldc/04-Syntheses/daily/trains-yachts-watch-daily-<date>.md
     in the second-brain vault.

SR-1: log_usage() in finally block.
SR-2: Exempt -- time-bounded input (last 1 day of RSS), inputs always new.
"""
import logging
import pathlib
import sqlite3
from datetime import date, datetime, timezone

from common import config
from common import entity_tracking
from common import ntfy_push
from common.llm import generate as llm_generate
from common.rss_retrieval import retrieve, format_citations
from common.sr1_log import log_usage
from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
from second_brain.scrub_gate import ScrubGateBlocked, gate

from poller.skills.aam_weekly_watch import _fetch_week_items

log = logging.getLogger(__name__)

SKILL_NAME = "trains-yachts-daily-watch"
OLLAMA_MODEL = "corporatetraveldc-pi5-trains-yachts-daily-watch:latest"  # dedicated Phase-4 model, persona + skill layer in its Modelfile SYSTEM
RSS_CATEGORY = "trains_yachts"
LOOKBACK_DAYS = 1

RETRIEVE_QUERY = (
    "rail railway train manufacturer shipyard shipbuilder yacht superyacht "
    "marine vessel launch delivery order fleet program rebuild refit"
)
RETRIEVE_TOP_N = 10

# 2026-09-02 split-call rearchitecture (see module docstring).
_SPLIT_TASK_PREAMBLE = """You are writing the daily rail/marine industry watch
section for an executive dispatch platform serving a boutique DC-area
executive services firm. You will be given today's raw rail and marine/
yacht industry headlines and a FRAMING INSTRUCTION at the end telling
you which single analytical framing to write.

Produce exactly ONE version:

TODAY'S DEVELOPMENTS: 2-5 sentences in the framing the FRAMING
INSTRUCTION asks for. If nothing today fits that framing, say so
plainly rather than manufacturing significance.

Plain text, no markdown headers beyond the label above, no filler. Cite
specific stories from the provided list -- do not invent developments
not present in the retrieved items. Once written, stop -- do not add
further versions, framings, or repetitions."""

_FRAMING_INSTRUCTIONS = {
    "ops": (
        "FRAMING INSTRUCTION: write TODAY'S DEVELOPMENTS focused on "
        "first-mover/ahead-of-the-curve relevance -- shipbuilder output "
        "shifts, new shipyard openings, next-gen rail program status "
        "changes, anything that signals where the industry is heading "
        "before it's obvious."
    ),
    "ep": (
        "FRAMING INSTRUCTION: write TODAY'S DEVELOPMENTS focused on "
        "anything with executive-travel or logistics relevance -- rail "
        "service disruptions, marine charter/access changes, anything "
        "that could affect client movement or positioning."
    ),
}


def _day_label(d: date) -> str:
    return d.isoformat()


def _generate_framing(flavor: str, base_prompt: str, headline_block: str) -> tuple[str, bool]:
    """One independent generation for one framing. See aam_daily_watch.py's
    module docstring for the full 2026-09-02 rationale."""
    result = llm_generate(
        system=None,
        prompt=f"{_SPLIT_TASK_PREAMBLE}\n\n{base_prompt}\n\n{_FRAMING_INSTRUCTIONS[flavor]}",
        ollama_model=OLLAMA_MODEL, max_tokens=250, temperature=0.25,
        timeout=1380, allow_anthropic=False,
    )
    if result:
        gated = gate(result, source=f"{SKILL_NAME}-llm-{flavor}")
        return gated, True
    try:
        fallback = (
            f"TODAY'S DEVELOPMENTS ({flavor} framing generation failed -- raw headlines):\n"
            + headline_block
        )
    except Exception as fallback_err:
        log.error("%s: %s fallback also failed — %s", SKILL_NAME, flavor, fallback_err)
        fallback = (
            f"[{SKILL_NAME.upper()}] {flavor} generation failed -- both the LLM "
            f"call and the deterministic fallback errored. See logs."
        )
    return fallback, False


def main() -> None:
    status = "error"
    today = date.today()
    rel_path = None

    try:
        items = _fetch_week_items(lookback_days=LOOKBACK_DAYS, category=RSS_CATEGORY)

        day_label_for_tracking = today.isoformat()
        tracking_summary = entity_tracking.run_tracking_pass(RSS_CATEGORY, items, day_label_for_tracking)
        if tracking_summary["auto_promoted"]:
            names = [e["name"] for e in tracking_summary["auto_promoted"]]
            log.info("%s: auto-promoted recurring entities: %s", SKILL_NAME, ", ".join(names))
            ntfy_push.send(
                "ops-health",
                f"New tracked source(s) auto-promoted for {RSS_CATEGORY}: {', '.join(names)}",
                title=f"{RSS_CATEGORY} cross-link auto-promote",
                priority=2, tags="link",
            )
        if tracking_summary["routed_to_review"]:
            log.info("%s: %d finding(s) routed to novel-findings review (00-Inbox/cross-link-findings/)",
                      SKILL_NAME, len(tracking_summary["routed_to_review"]))
        boost_terms = entity_tracking.get_boost_terms(RSS_CATEGORY)
        retrieve_query = f"{RETRIEVE_QUERY} {boost_terms}".strip() if boost_terms else RETRIEVE_QUERY

        retrieved = retrieve(items, retrieve_query, RETRIEVE_TOP_N)
        headline_block = format_citations(retrieved) or (
            "(no items in the last 24 hours -- quiet day for this category)"
        )

        prompt = (
            f"TODAY'S MOST RELEVANT RAIL/MARINE DEVELOPMENTS ({len(retrieved)} of "
            f"{len(items)} items retrieved, last {LOOKBACK_DAYS} day -- "
            f"cite these directly, do not invent sources not listed here):"
            f"\n{headline_block}"
        )

        # 2026-09-02: two independent per-framing calls, joined below --
        # see module docstring / aam_daily_watch.py for the full rationale.
        ops_synthesis, ops_ok = _generate_framing("ops", prompt, headline_block)
        ep_synthesis, ep_ok = _generate_framing("ep", prompt, headline_block)
        if ops_ok and ep_ok:
            status = "ok"
            log.info("%s: both framings generated independently via %s (%d of %d items retrieved)",
                      SKILL_NAME, OLLAMA_MODEL, len(retrieved), len(items))
        elif ops_ok or ep_ok:
            status = "partial"
            log.info("%s: %s framing generated, %s framing fell back to raw headlines",
                      SKILL_NAME, "ops" if ops_ok else "ep", "ep" if ops_ok else "ops")
        else:
            status = "fallback"
            log.info("%s: both framing generations failed -- raw headline fallback for both flavors",
                      SKILL_NAME)

        day_label = _day_label(today)
        generated_at = datetime.now(timezone.utc).isoformat()
        header = f"TRAINS & YACHTS DAILY WATCH -- {day_label} (generated {generated_at})\n\n"
        ops_full = header + ops_synthesis.strip() + "\n"
        ep_full = header + ep_synthesis.strip() + "\n"

        state = pathlib.Path(config.state_dir())
        state.mkdir(parents=True, exist_ok=True)
        (state / "trains_yachts_daily_watch_ops.txt").write_text(ops_full)
        (state / "trains_yachts_daily_watch_ep.txt").write_text(ep_full)
        log.info("%s: wrote ops + ep caches", SKILL_NAME)

        frontmatter = (
            "---\n"
            f"date: {day_label}\n"
            "ingest_method: trains-yachts-daily-watch\n"
            f"generated_at: {generated_at}\n"
            f"rss_items: {len(items)}\n"
            f"rss_items_retrieved: {len(retrieved)}\n"
            "---\n\n"
        )
        note = (
            frontmatter
            + f"# Trains & Yachts Daily Watch — {day_label}\n\n"
            + "## Ops framing\n\n" + ops_synthesis.strip() + "\n\n"
            + "## EP framing\n\n" + ep_synthesis.strip() + "\n"
        )
        rel_path = f"{webdav_client.BUSINESS_ROOT}/04-Syntheses/daily/trains-yachts-watch-daily-{day_label}.md"
        webdav_client.put(rel_path, note)

        conn = sqlite3.connect(INDEX_DB)
        init_vault_db(conn)
        index_note(
            conn, rel_path, title=f"Trains & Yachts Daily Watch — {day_label}", content=note,
            tags="daily,trains,yachts,marine,rail,synthesis,auto",
            ingest_method="trains-yachts-daily-watch",
        )
        conn.close()
        log.info("%s: wrote %s (status=%s)", SKILL_NAME, rel_path, status)

    except ScrubGateBlocked as e:
        status = "blocked"
        log.error("%s: BLOCKED by scrub gate: %s", SKILL_NAME, e)
    finally:
        log_usage(SKILL_NAME, OLLAMA_MODEL if status in ("ok", "partial") else "deterministic",
                   0, 0, status, "new")
        ntfy_push.send_run_status(SKILL_NAME, status, detail=rel_path,
                                  ok_statuses=("ok", "partial", "fallback"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
