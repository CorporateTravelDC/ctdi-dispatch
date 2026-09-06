"""
aam_daily_watch -- daily sibling of aam_weekly_watch.py. Same sourcing
(advanced_air_mobility RSS category), same underlying model -- just a
1-day lookback instead of 7, run every 90 minutes instead of weekly.

2026-09-02 (operator-directed rearchitecture): the ops and EP framings
are now TWO INDEPENDENT llm_generate() calls joined afterward, not one
shared call mechanically split via _split_framings(). Root cause of the
change: a live audit found this skill falling back on ~83% of recent
runs, and 21 of 24 classified fallback events were common.llm's
repetition-loop content guard discarding the single shared response --
NOT Ollama being unavailable. Mechanism: the shared-call persona
instructed the model to write "the same status summary" twice (once per
framing); on quiet AAM news days (frequent for this category -- often
zero items in the 24h window) both framings degenerate into
near-identical restatements of the status block, phi3:mini then loops a
third framing block until it hits the 700-token cap, and the >=3x
identical->40-char-line guard (correctly) flags the loop -- but discards
the ENTIRE response, valid framings included, and both flavors fall
back together. Per-framing calls remove the instructed duplication
(nothing legitimate is ever written twice in one response), halve the
per-call output budget (350 vs 700 -- cap-hit looping is what preceded
every guard discard), and give each framing its own fallback so one bad
generation no longer takes the other down. Total generation tokens are
unchanged (2x350 vs 1x700); the added cost is one extra prompt-eval
pass, mitigated by sharing an identical prompt prefix across the two
calls (llama-server prefix cache; the framing instruction is appended
at the END of the user message for exactly this reason).

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

Schedule: every 90 minutes, anchored 07:30 ET (corporatetraveldc-
aam-daily-watch.timer; fixed 24h calendar grid since 2026-08-17, see
the timer's own comment) -- after daily-opsplan (07:00) and clear of
the hourly ops-brief/ep-advance Ollama-slot marks (:00/:30) by 30
minutes either side, same stagger principle as every other skill here.

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
from common.personas import PERSONAS
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

# 2026-09-02 split-call rearchitecture (see module docstring): each call
# writes ONE framing, so the persona registry's baked two-framings task
# layer (PERSONAS["aam-daily-watch"]["task"], still used by nothing else
# after this change but left untouched -- it stays verbatim-synced with
# the canonical corporatetraveldc.aam-daily-watch source text) is
# replaced per-call via generate()'s explicit system= override. The
# shared dispatcher preamble is pulled from the registry rather than
# copied, so it can't drift from what every other skill uses.
_SPLIT_TASK_LAYER = """This call serves the aam-daily-watch skill. On top of the shared
dispatcher identity above: you are writing ONE framing of the daily
Advanced Air Mobility watch for this platform. You will be given a
maintained "current status" block, a list of today's raw RSS headlines
from AAM/vertiport/UAS trade press, and a FRAMING INSTRUCTION at the
end of the message telling you which single analytical framing to
write.

Produce exactly ONE version with these two labeled sub-sections, each
label on its own line, in this exact order, each written exactly once:

WHAT MATTERS TODAY: a tight 2-4 sentence summary of the current status
block, in plain operational language.
TODAY'S DEVELOPMENTS: 2-5 sentences in the framing the FRAMING
INSTRUCTION asks for. If nothing today is relevant to that framing, say
so plainly rather than manufacturing significance.

Plain text within each section, no markdown headers beyond the labels
above, no filler. Cite specific stories from the provided list -- do
not invent developments not present in the retrieved items. Once both
sections are written, stop -- do not add further versions, framings, or
repetitions."""

# Appended to the END of the otherwise-identical user prompt (not the
# front, and not in system=) so the two calls share the longest possible
# prefix for llama-server's prompt cache -- the second call's prompt
# eval is then nearly free instead of a full second pass.
_FRAMING_INSTRUCTIONS = {
    "ops": (
        "FRAMING INSTRUCTION: write TODAY'S DEVELOPMENTS focused on "
        "logistics and ground-transport relevance -- route planning, "
        "ground infrastructure timing, airspace advisories that could "
        "affect existing chauffeur operations near DCA/IAD/BWI."
    ),
    "ep": (
        "FRAMING INSTRUCTION: write TODAY'S DEVELOPMENTS focused on the "
        "executive-protection and security angle -- new low-altitude air "
        "traffic as a surveillance or access consideration, counter-UAS "
        "relevance, VIP movement exposure, or security-adjacent "
        "regulatory activity."
    ),
}


def _day_label(d: date) -> str:
    return d.isoformat()


def _generate_framing(flavor: str, base_prompt: str, headline_block: str) -> tuple[str, bool]:
    """One independent generation for one framing ("ops" or "ep").
    Returns (text, generated_ok). generated_ok=False means the text is
    this framing's own deterministic raw-headline fallback -- the other
    framing's call is unaffected either way (the whole point of the
    2026-09-02 split, see module docstring). ScrubGateBlocked propagates
    to main()'s existing handler unchanged -- a scrub block is a security
    event and still aborts the whole run, not just one flavor."""
    result = llm_generate(
        system=PERSONAS["aam-daily-watch"]["preamble"] + "\n\n" + _SPLIT_TASK_LAYER,
        prompt=f"{base_prompt}\n\n{_FRAMING_INSTRUCTIONS[flavor]}",
        ollama_model=OLLAMA_MODEL, max_tokens=350, temperature=0.25,
        # Derived from the same 2026-08-15 forced-contention measurement
        # that produced the old shared call's 1890s (see git history of
        # this file): 53s locked bound + 248.3s prompt eval + 350 tok at
        # 0.64 tok/s = 546.9s gen, x1.25 = 1060 -> 1200 with margin.
        # Worst case both framings time out: 2x1200 + entity extraction
        # still clears the quadlet's TimeoutStartSec=8600 and fits inside
        # one 90-min timer slot.
        timeout=1200,
        allow_anthropic=False,
    )
    if result:
        gated = gate(result, source=f"{SKILL_NAME}-llm-{flavor}")
        return gated, True
    # Same narrow safety-net-around-the-fallback pattern applied
    # identically across every skill with an Ollama fallback (2026-08-06)
    # -- see route_impact.py for the full note. Per-framing now.
    try:
        fallback = (
            "WHAT MATTERS TODAY:\n" + _TODAY_STATUS.strip() +
            f"\n\nTODAY'S DEVELOPMENTS ({flavor} framing generation failed"
            " -- raw headlines):\n" + headline_block
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
    rel_path = None
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

        base_prompt = (
            f"CURRENT STATUS (as of {_TODAY_STATUS_ASOF}):\n{_TODAY_STATUS}\n\n"
            f"TODAY'S MOST RELEVANT DEVELOPMENTS ({len(retrieved)} of "
            f"{len(items)} items retrieved, last {LOOKBACK_DAYS} day -- "
            f"cite these directly, do not invent sources not listed here):"
            f"\n{headline_block}"
        )

        # 2026-09-02: two independent per-framing calls, joined below --
        # see module docstring for the root cause (repetition-loop guard
        # discards of the old single shared 700-token call) and the
        # cost/caching reasoning. Ops first, EP immediately after, so the
        # second call lands on the still-warm prompt-cache prefix.
        ops_synthesis, ops_ok = _generate_framing("ops", base_prompt, headline_block)
        ep_synthesis, ep_ok = _generate_framing("ep", base_prompt, headline_block)
        if ops_ok and ep_ok:
            status = "ok"
            log.info("%s: both framings generated independently via %s (%d of %d items retrieved)",
                     SKILL_NAME, OLLAMA_MODEL, len(retrieved), len(items))
        elif ops_ok or ep_ok:
            status = "partial"
            log.info("%s: %s framing generated, %s framing fell back to raw headlines "
                     "(independent calls -- one bad generation no longer takes both down)",
                     SKILL_NAME, "ops" if ops_ok else "ep", "ep" if ops_ok else "ops")
        else:
            status = "fallback"
            log.info("%s: both framing generations failed -- raw headline fallback for both flavors",
                     SKILL_NAME)

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
        # "partial" (2026-09-02, split-call rearchitecture): one framing
        # generated, the other fell back -- the model DID run, so credit
        # the model column; the status column still distinguishes it from
        # a clean "ok" for fallback-rate auditing.
        log_usage(SKILL_NAME, OLLAMA_MODEL if status in ("ok", "partial") else "deterministic",
                   0, 0, status, "new")
        ntfy_push.send_run_status(SKILL_NAME, status, detail=rel_path,
                                  ok_statuses=("ok", "partial", "fallback"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
