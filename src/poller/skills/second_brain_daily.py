"""
second_brain_daily -- daily ingest into the second-brain vault.

Pulls the day's operational picture (CPS distribution, TFR/NOTAM/NWS alert
counts, latest ops-brief text, METAR snapshot, Amtrak status, watchlist
activity) into a dated note under corporatetraveldc/01-Sources/daily/ in
the Nextcloud vault. Runs through the CUI/PII scrub gate before writing --
non-negotiable per the original second-brain plan (2026-07-18).

Schedule: daily at 23:45 ET (corporatetraveldc-second-brain-daily.timer) --
late enough to capture nearly the full day, ahead of the weekly compile's
Sunday 18:15 ET window.

Model: same tiered pattern as weekly_summary.py -- Ollama first (cheap,
local), deterministic fallback if unavailable. SR-1 compliant (log_usage).

2026-08-07: also surfaces same-day common.export_analysis digests (see
that module's docstring for the full file-handling policy) in the daily
rollup, same "Latest X excerpt" pattern already used for ops-brief below
-- this is how export-analysis output satisfies the operator directive
that it "feed into second-brain daily," alongside the weekly compile
already picking it up automatically via the existing 04-Syntheses/daily
scan (second_brain_weekly.py, 2026-08-06 fix).

2026-08-12: closes a real gap found while adding the COS26/conference
OSINT scopes -- osint_monitor.py's scored feed (EP/security scopes,
marketing/brand scopes, and the new "event" scope_type covering DC-area
conferences) was never read by ANYTHING in the second-brain vault. It
already reached ep_advance_brief.py (unscoped osint_get_feed() call
there), but the vault itself had zero visibility into it. _osint_sections()
below pulls the same feed but groups it by scope_type into separate,
clearly-labeled sections (EP/security, upcoming events, market/brand
intel, general) rather than one undifferentiated dump -- an EP threat
item and a conference-marketing item are relevant to different future
queries against this vault, and merging them would bury both. No new
second_brain_weekly.py changes needed: it re-reads the past 7 days of
THIS skill's own output rather than querying the DB itself, so whatever
lands in the daily note here is already inherited by the weekly compile.
"""
import logging
import sqlite3
import time
from collections import Counter
from datetime import date, datetime, timezone

from common import db
from common import ntfy_push
from common.llm import generate as llm_generate
from common.sr1_log import log_usage
from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
from second_brain.scrub_gate import ScrubGateBlocked, gate

log = logging.getLogger(__name__)

SKILL_NAME = "second-brain-daily"
OLLAMA_MODEL = "corporatetraveldc-pi5-secondbrain-daily:latest"  # dedicated Phase-4 model, persona + skill layer in its Modelfile SYSTEM

# Same scope_type groupings as osint_monitor.py -- kept as a separate copy
# rather than importing that module's private frozensets, matching this
# codebase's existing preference for small duplicated constants over a
# cross-skill import for something this narrow (see route_impact.py's
# repeated fallback-safety-net comment for the same pattern elsewhere).
_EP_SCOPE_TYPES = frozenset({
    "ep_threat", "ep_principal", "ep_venue", "executive_protection",
})
_EVENT_SCOPE_TYPES = frozenset({"event"})
_MARKETING_SCOPE_TYPES = frozenset({
    "brand_monitor", "market_intel", "competitor", "marketing",
})

SYSTEM_PROMPT = """You are writing a single day's operational log entry for a
second-brain knowledge vault used by a DC-area executive chauffeur/dispatch
operation. Summarize the day's operational picture described in the prompt
in under 300 words, plain prose paragraphs only.

Critical rules:
- The prompt's first line states the real date being summarized. Do not
  invent, guess, or restate a different date anywhere in your output.
- Do not add a title, heading, or dateline of any kind (no "#", "##", or
  similar) -- the note this becomes already has its own date in the
  surrounding document. Start directly with the first sentence of prose.
- Every number in the prompt (counts, delay minutes, station counts, etc.)
  must be quoted EXACTLY as given -- same digits, same value. Do not round,
  abbreviate, spell out, or restate a number differently than it appears
  in the prompt.
- Note anything a future weekly compile pass would want to link to
  (notable TFRs, weather events, CPS trend, watchlist activity).
- Be factual, not promotional."""


def _osint_sections(cutoff_ts: float) -> tuple[list[str], int]:
    """Cross-domain OSINT rollup for the vault, grouped by scope_type so an
    EP/security item, a named-event item (COS26 and the DC-area conference
    sweep), and a marketing/brand item each land in their own labeled
    section instead of one undifferentiated list -- see module docstring,
    2026-08-12.

    Returns (section_texts, total_item_count). Best-effort: returns ([], 0)
    on any failure (DB unreachable, no scopes configured) rather than
    blocking the rest of the daily rollup."""
    try:
        items = db.osint_get_feed(scope_id=None, min_score=4, limit=100)
    except Exception as exc:
        log.debug("%s: osint feed read failed (non-fatal): %s", SKILL_NAME, exc)
        return [], 0

    recent = [i for i in items if i.get("ingested_at", 0) >= cutoff_ts]
    if not recent:
        return [], 0

    ep_items  = [i for i in recent if i.get("scope_type") in _EP_SCOPE_TYPES]
    evt_items = [i for i in recent if i.get("scope_type") in _EVENT_SCOPE_TYPES]
    mkt_items = [i for i in recent if i.get("scope_type") in _MARKETING_SCOPE_TYPES]
    seen_ids  = {i["id"] for i in ep_items + evt_items + mkt_items}
    gen_items = [i for i in recent if i["id"] not in seen_ids]

    def _fmt(group: list[dict], header: str) -> str:
        lines = [
            f"- [{i.get('score_label', '?')}] {i.get('scope_label', '?')}: "
            f"{i['title'][:100]}"
            + (f" — {i['narrative'][:150]}" if i.get("narrative") else "")
            for i in group[:8]
        ]
        return f"{header} ({len(group)} item(s) today):\n" + "\n".join(lines)

    out = []
    if ep_items:
        out.append(_fmt(ep_items, "EP/security-relevant OSINT"))
    if evt_items:
        out.append(_fmt(evt_items, "Upcoming DC-area event intel"))
    if mkt_items:
        out.append(_fmt(mkt_items, "Market/brand intelligence"))
    if gen_items:
        out.append(_fmt(gen_items, "General OSINT"))
    return out, len(recent)


def build_daily_content() -> tuple[str, dict]:
    day_ago = time.time() - 86400
    with db.conn() as c:
        cps_rows = c.execute(
            "SELECT score, label, computed_at FROM cps_scores "
            "WHERE computed_at >= ? ORDER BY computed_at DESC",
            (day_ago,),
        ).fetchall()
        tfr_rows = c.execute(
            "SELECT tfr_id, is_vip FROM tfrs WHERE inserted_at >= ?",
            (day_ago,),
        ).fetchall()

    cps_counts = Counter(r["score"] for r in cps_rows)
    vip_tfrs = [r for r in tfr_rows if r["is_vip"]]

    notams = db.get_active_notams()
    nws_alerts = db.get_active_nws_alerts()
    amtrak = db.get_latest_amtrak_status()
    metars = db.get_metar_snapshot()
    watchlists = db.get_active_watchlists()
    brief_history = db.get_brief_history(limit=1, brief_type="ops")

    stats = {
        "cps_readings": len(cps_rows),
        "cps_distribution": dict(cps_counts),
        "tfrs_seen": len(tfr_rows),
        "vip_tfrs": len(vip_tfrs),
        "active_notams": len(notams),
        "active_nws_alerts": len(nws_alerts),
        "active_watchlists": len(watchlists),
    }

    sections = [
        f"Date being summarized: {date.today().isoformat()}",
        f"CPS readings today: {len(cps_rows)} ({dict(cps_counts)})",
        f"TFRs seen today: {len(tfr_rows)} total, {len(vip_tfrs)} VIP/POTUS",
        f"Active NOTAMs: {len(notams)}",
        f"Active NWS alerts: {len(nws_alerts)}",
        f"Active watchlist sessions: {len(watchlists)}",
    ]
    if amtrak and amtrak.get("delay_summary"):
        sections.append(f"Amtrak: {amtrak['delay_summary']}")
    if metars:
        vfr_count = sum(1 for m in metars if m.get("flight_category") == "VFR")
        sections.append(f"METAR snapshot: {len(metars)} stations, {vfr_count} VFR")
    if brief_history:
        latest = brief_history[0]
        excerpt = (latest.get("content") or "")[:300]
        if excerpt:
            sections.append("Latest ops-brief excerpt:\n" + excerpt)

    # 2026-08-07: same-day export-analysis digest(s), if any -- see
    # common.export_analysis's docstring for the full policy. Best-effort,
    # never fatal if the vault read fails or nothing exists today.
    try:
        today_str = date.today().isoformat()
        export_note = webdav_client.get(
            f"{webdav_client.BUSINESS_ROOT}/04-Syntheses/daily/export-analysis-linkedin-{today_str}.md"
        )
        if export_note:
            excerpt = export_note.decode("utf-8", errors="replace")[:400]
            sections.append("Today's export-analysis digest:\n" + excerpt)
    except Exception:
        pass  # no digest today, or vault unreachable -- not fatal to the daily rollup

    # 2026-08-12: cross-domain OSINT rollup -- see _osint_sections() and
    # module docstring for why this is grouped by scope_type rather than
    # one flat list.
    osint_sections, osint_count = _osint_sections(day_ago)
    sections.extend(osint_sections)
    stats["osint_items_today"] = osint_count

    return "\n\n".join(sections), stats


def main() -> None:
    gate_result = "new"
    status = "error"
    today = date.today().isoformat()
    rel_path = None

    try:
        raw_content, stats = build_daily_content()

        # CUI/PII scrub gate -- non-negotiable, see second_brain.scrub_gate
        raw_content = gate(raw_content, source=SKILL_NAME)

        ollama_result = llm_generate(
            system=None, prompt=raw_content,  # dedicated Modelfile carries this now
            ollama_model=OLLAMA_MODEL, max_tokens=350, temperature=0.3,
            # Measured 2026-08-15 under forced TIER2+ contention (Phase-3
            # methodology: guard timer paused, synthetic burn, la 44 at
            # sample): 1156-tok prompt / 138.2s eval + gen at 0.75 tok/s
            # -> 464.2s at the 350-tok cap; delta over the 47.1s
            # spiked persona-only ref = 555.4s; x1.13 top-up to the 53s locked bound applied;
            # (53 + 625.5) x 1.25 = 848s -> 870.
            timeout=870,
            # 2026-08-12: belt-and-suspenders close of the Anthropic
            # fallback -- see dispatch.env's ANTHROPIC_FALLBACK_ENABLED
            # comment for the full rationale.
            allow_anthropic=False,
        )
        if ollama_result:
            ollama_result = gate(ollama_result, source=f"{SKILL_NAME}-llm")
            narrative = ollama_result
            status = "ok"
            log.info("%s: narrative generated via Ollama/%s", SKILL_NAME, OLLAMA_MODEL)
        else:
            # 2026-08-06: narrow safety net around the fallback ITSELF --
            # same pattern applied identically across every skill with an
            # Ollama fallback. See route_impact.py for the full note.
            try:
                narrative = raw_content
                status = "fallback"
                log.info("%s: Ollama unavailable -- using deterministic content", SKILL_NAME)
            except Exception as fallback_err:
                log.error("%s: fallback also failed — %s", SKILL_NAME, fallback_err)
                narrative = (
                    f"[{SKILL_NAME.upper()}] Generation failed -- both Ollama and the "
                    f"deterministic fallback errored. See logs."
                )
                status = "fallback_error"

        frontmatter = (
            "---\n"
            f"date: {today}\n"
            "ingest_method: daily-auto\n"
            f"generated_at: {datetime.now(timezone.utc).isoformat()}\n"
            f"stats: {stats}\n"
            "---\n\n"
        )
        note = frontmatter + f"# Daily Log — {today}\n\n" + narrative + "\n"

        rel_path = f"{webdav_client.BUSINESS_ROOT}/01-Sources/daily/{today}.md"
        webdav_client.put(rel_path, note)
        log.info("%s: wrote %s (%d bytes, status=%s)", SKILL_NAME, rel_path, len(note), status)

        conn = sqlite3.connect(INDEX_DB)
        init_vault_db(conn)
        index_note(
            conn, rel_path, title=f"Daily Log — {today}", content=note,
            tags="daily,auto", ingest_method="daily-auto",
        )
        conn.close()

    except ScrubGateBlocked as e:
        status = "blocked"
        log.error("%s: BLOCKED by scrub gate: %s", SKILL_NAME, e)
    finally:
        log_usage(SKILL_NAME, OLLAMA_MODEL if status == "ok" else "deterministic",
                   0, 0, status, gate_result)
        ntfy_push.send_run_status(SKILL_NAME, status, detail=rel_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
