"""
aam_daily_watch -- daily sibling of aam_weekly_watch.py. Same sourcing
(advanced_air_mobility RSS category), same synthesis approach (status
block + Ollama split into ops/EP framings), same underlying model --
just a 1-day lookback instead of 7, run daily instead of weekly.

Added 2026-08-06 per operator request, alongside verifying the weekly
skill's actual live health (see docs/AAM_WATCH_STATUS.md if present, or
the 2026-08-06 chat history -- the weekly skill's timer turned out to be
correctly configured and genuinely firing on schedule, but Persistent=true
was also firing it on every reboot this week, racing the runner
container before it was up and repeatedly overwriting the hourly-brief
cache with an empty-fallback result; fixed alongside this addition).

Deliberately does NOT feed common.aam_watch's ops-brief/ep-advance
hourly-brief cache -- that's already served by the weekly job, and
overwriting it daily would defeat the weekly cadence that skill was
built for (see its own docstring: "no reason to burn an Ollama cycle on
it every run"). This is a genuinely separate, standalone daily digest:
its own cache files (for potential future consumers), its own vault
entry, own SR-1 log line. Same reasoning as second_brain_daily.py vs.
second_brain_weekly.py being real siblings rather than one skill with
two schedules -- daily and weekly here have different jobs (fresh daily
digest vs. weekly rollup+trend), not one replacing the other.

No ntfy push -- matches the established convention for this class of
skill (second_brain_daily.py, second_brain_weekly.py, aam_weekly_watch.py
itself all write to cache/vault only, no direct alert; weekly_summary.py
is the exception that does push, and it's a different kind of report).

Schedule: daily 07:30 ET (corporatetraveldc-aam-daily-watch.timer) --
after daily-opsplan (07:00) and clear of the hourly ops-brief/ep-advance
Ollama-slot marks (:00/:30) by 30 minutes either side, same stagger
principle as every other skill here.

Output:
  1. aam_daily_watch_ops.txt / aam_daily_watch_ep.txt in state_dir() --
     own cache, NOT read by common.aam_watch (see above).
  2. corporatetraveldc/04-Syntheses/daily/aam-watch-daily-<date>.md in
     the second-brain vault -- durable daily record, parallel to the
     weekly job's 04-Syntheses/weekly/ entries.

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

from poller.skills.aam_weekly_watch import (
    RETRIEVE_QUERY,
    RSS_CATEGORY,
    _TODAY_STATUS,
    _TODAY_STATUS_ASOF,
    _fetch_week_items,
    _split_framings,
)

log = logging.getLogger(__name__)

SKILL_NAME = "aam-daily-watch"
# Phase 4 2026-08-15: no longer shares the weekly skill's model -- own
# dedicated model (and Modelfile SYSTEM adapted to daily framing).
OLLAMA_MODEL = "corporatetraveldc-pi5-aam-daily-watch:latest"
LOOKBACK_DAYS = 1
# Smaller than the weekly sibling's 20 -- a 1-day corpus is much smaller
# to begin with, so this just trims genuine noise rather than doing real
# corpus-size reduction the way the weekly retrieval does.
RETRIEVE_TOP_N = 10


def _day_label(d: date) -> str:
    return d.isoformat()


def main() -> None:
    status = "error"
    today = date.today()

    try:
        items = _fetch_week_items(lookback_days=LOOKBACK_DAYS)

        # 2026-08-06: cross-link/recurring-source detection -- see
        # common.entity_tracking module docstring for the full design.
        # Non-fatal by construction (run_tracking_pass never raises); a
        # broken extraction must never take down the main brief.
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

        # 2026-08-06: retrieve the most relevant subset rather than dump
        # every headline -- same rss_retrieval mechanism as the weekly
        # sibling, see that module's own comment for the full rationale.
        retrieved = retrieve(items, retrieve_query, RETRIEVE_TOP_N)
        headline_block = format_citations(retrieved) or (
            "(no items in the last 24 hours -- quiet day for this category)"
        )

        prompt = (
            f"CURRENT STATUS (as of {_TODAY_STATUS_ASOF}):\n{_TODAY_STATUS}\n\n"
            f"TODAY'S MOST RELEVANT DEVELOPMENTS ({len(retrieved)} of "
            f"{len(items)} items retrieved, last {LOOKBACK_DAYS} day -- "
            f"cite these directly, do not invent sources not listed here):"
            f"\n{headline_block}"
        )

        ollama_result = llm_generate(
            system=None, prompt=prompt,  # dedicated Modelfile carries this now
            ollama_model=OLLAMA_MODEL, max_tokens=700, temperature=0.25,
            # Measured 2026-08-15 under forced TIER2+ contention (Phase-3
            # methodology: guard timer paused, synthetic burn, la 35 at
            # sample): 1910-tok prompt / 248.3s eval + gen at 0.64 tok/s
            # -> 1093.2s at the 700-tok cap; delta over the 47.1s
            # spiked persona-only ref = 1294.4s; x1.13 top-up to the 53s locked bound applied;
            # (53 + 1457.8) x 1.25 = 1889s -> 1890.
            timeout=1890,
            # allow_anthropic=False keeps this Ollama-only (no silent cloud
            # fallback/cost). max_retries=2 (2026-08-07): a model-swap/cold-load
            # transport blip retries (up to 2x, each a fresh 240s) instead of
            # falling straight back -- 3x240=720s still fits TimeoutStartSec=950.
            allow_anthropic=False, max_retries=2,
        )
        if ollama_result:
            gated = gate(ollama_result, source=f"{SKILL_NAME}-llm")
            ops_synthesis, ep_synthesis = _split_framings(gated)
            status = "ok"
            log.info("%s: split synthesis generated via Ollama/%s (%d of %d items retrieved)",
                     SKILL_NAME, OLLAMA_MODEL, len(retrieved), len(items))
        else:
            # Same narrow safety-net-around-the-fallback pattern applied
            # identically across every skill with an Ollama fallback
            # (2026-08-06) -- see route_impact.py for the full note.
            try:
                fallback = (
                    "WHAT MATTERS TODAY:\n" + _TODAY_STATUS.strip() +
                    "\n\nTODAY'S DEVELOPMENTS (Ollama unavailable -- raw headlines):\n" +
                    headline_block
                )
                ops_synthesis = ep_synthesis = fallback
                status = "fallback"
                log.info("%s: Ollama unavailable -- using raw headline fallback for both flavors",
                         SKILL_NAME)
            except Exception as fallback_err:
                log.error("%s: fallback also failed — %s", SKILL_NAME, fallback_err)
                ops_synthesis = ep_synthesis = (
                    f"[{SKILL_NAME.upper()}] Generation failed -- both Ollama and the "
                    f"deterministic fallback errored. See logs."
                )
                status = "fallback_error"

        day_label = _day_label(today)
        generated_at = datetime.now(timezone.utc).isoformat()
        header = f"ADVANCED AIR MOBILITY DAILY WATCH -- {day_label} (generated {generated_at})\n\n"
        ops_full = header + ops_synthesis.strip() + "\n"
        ep_full = header + ep_synthesis.strip() + "\n"

        # 1. Own cache -- separate filenames from the weekly job's, and
        # deliberately not read by common.aam_watch (see module docstring).
        state = pathlib.Path(config.state_dir())
        state.mkdir(parents=True, exist_ok=True)
        (state / "aam_daily_watch_ops.txt").write_text(ops_full)
        (state / "aam_daily_watch_ep.txt").write_text(ep_full)
        log.info("%s: wrote ops + ep caches", SKILL_NAME)

        # 2. Durable copy in the second-brain vault -- both framings.
        frontmatter = (
            "---\n"
            f"date: {day_label}\n"
            "ingest_method: aam-daily-watch\n"
            f"generated_at: {generated_at}\n"
            f"rss_items: {len(items)}\n"
            "---\n\n"
        )
        note = (
            frontmatter
            + f"# Advanced Air Mobility Daily Watch — {day_label}\n\n"
            + "## Ops framing\n\n" + ops_synthesis.strip() + "\n\n"
            + "## EP framing\n\n" + ep_synthesis.strip() + "\n"
        )
        rel_path = f"{webdav_client.BUSINESS_ROOT}/04-Syntheses/daily/aam-watch-daily-{day_label}.md"
        webdav_client.put(rel_path, note)

        conn = sqlite3.connect(INDEX_DB)
        init_vault_db(conn)
        index_note(
            conn, rel_path, title=f"AAM Daily Watch — {day_label}", content=note,
            tags="daily,aam,vertiport,evtol,synthesis,auto",
            ingest_method="aam-daily-watch",
        )
        conn.close()
        log.info("%s: wrote %s (status=%s)", SKILL_NAME, rel_path, status)

    except ScrubGateBlocked as e:
        status = "blocked"
        log.error("%s: BLOCKED by scrub gate: %s", SKILL_NAME, e)
    finally:
        log_usage(SKILL_NAME, OLLAMA_MODEL if status == "ok" else "deterministic",
                   0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
