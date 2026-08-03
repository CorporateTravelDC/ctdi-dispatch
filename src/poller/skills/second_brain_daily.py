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
"""
import logging
import sqlite3
import time
from collections import Counter
from datetime import date, datetime, timezone

from common import db
from common.llm import generate as llm_generate
from common.sr1_log import log_usage
from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
from second_brain.scrub_gate import ScrubGateBlocked, gate

log = logging.getLogger(__name__)

SKILL_NAME = "second-brain-daily"
OLLAMA_MODEL = "corporatetraveldc-pi5-secondbrain-daily:latest"

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

    return "\n\n".join(sections), stats


def main() -> None:
    gate_result = "new"
    status = "error"
    today = date.today().isoformat()

    try:
        raw_content, stats = build_daily_content()

        # CUI/PII scrub gate -- non-negotiable, see second_brain.scrub_gate
        raw_content = gate(raw_content, source=SKILL_NAME)

        ollama_result = llm_generate(
            system=None, prompt=raw_content,  # dedicated Modelfile carries this now
            ollama_model=OLLAMA_MODEL, max_tokens=350, temperature=0.3,
            # Explicit timeout added 2026-07-26: smallest prompt of the
            # group, not yet independently timed, but was silently
            # inheriting the shared OLLAMA_TIMEOUT=60s tuned for a
            # different, faster skill chain -- same failure class as
            # aam_weekly_watch/dispatch_desk_memo. 300s is conservative
            # headroom under the container's TimeoutStartSec=950 pending a
            # real measurement.
            timeout=300,
        )
        if ollama_result:
            ollama_result = gate(ollama_result, source=f"{SKILL_NAME}-llm")
            narrative = ollama_result
            status = "ok"
            log.info("%s: narrative generated via Ollama/%s", SKILL_NAME, OLLAMA_MODEL)
        else:
            narrative = raw_content
            status = "fallback"
            log.info("%s: Ollama unavailable -- using deterministic content", SKILL_NAME)

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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
