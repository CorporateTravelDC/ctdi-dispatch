"""
entity_tracking_digest -- standalone 6-hourly consolidated surface of
common.entity_tracking's findings into the second-brain vault.

Real gap this closes, found 2026-08-23: entity_tracking.py is a shared
library, not its own skill -- it's called FROM WITHIN each of the six
daily-watch skills (aam, aviation, concierge-travel, executive-protection,
gig-economy, trains-yachts) and osint_monitor, and writes its own findings
directly to the vault as it goes (00-Inbox/cross-link-findings/ for novel/
sub-threshold entities, 03-Entities/ on auto-promotion -- see that
module's docstring). Those writes are real and immediate, but nothing
ever aggregated them into one place a human or the weekly compile would
actually look at -- confirmed via grep, second_brain_daily.py has zero
references to cross-link-findings or entity_tracking. A finding only
ever surfaced if someone happened to search the vault or stumble onto
the individual per-entity note.

This skill does NOT re-run entity_tracking or duplicate its writes -- it
purely queries second_brain.index_db's vault_documents table (already
populated by entity_tracking.py's own index_note() calls, ingest_method
"entity-tracking-novel-finding" / "entity-tracking-promotion") for
anything indexed since the last digest, and writes one consolidated,
deterministic summary note. Deliberately NOT an Ollama call -- these are
already-structured findings (entity, category, status, path), a plain
listing needs no synthesis, and every existing daily/weekly digest in
this codebase already competes for the single Ollama slot; adding a
seventh consumer for a job that doesn't need generation would just be
more contention for no benefit (see tonight's 2026-08-22/23 CLAUDE.md
incident writeup on bandwidth_priority/pre-flight-load-gate stacking).

Schedule: every 6h (corporatetraveldc-entity-tracking-digest.timer),
fixed calendar grid at :12 past 00/06/12/18 -- offset off every other
timer's :00/:15/:30/:45 marks on purpose, same lesson trains-yachts'
timer comment documents (OnUnitActiveSec bunching converges timers onto
the same second over time; a fixed calendar grid with a genuinely
distinct offset doesn't).

Output: corporatetraveldc/04-Syntheses/entity-tracking/<UTC timestamp>.md
-- deliberately NOT under 01-Sources/daily or 04-Syntheses/daily (both
date-keyed, one-file-per-day conventions second_brain_weekly.py's scan
logic assumes); a separate, clearly-named location keeps this from
interfering with that logic while still being immediately indexed and
searchable via index_db.py --search.

No-op (no vault write, no cursor advance past "nothing new") when
nothing has been indexed since the last digest -- avoids empty noise
notes.

SR-1: log_usage() in a finally block, model="none" (no LLM call made).
SR-2: not applicable -- this skill has no content-bearing input to hash;
its behavior is entirely "what's new in vault_documents since last run".
"""
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from common import config
from common.sr1_log import log_usage
from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note, init_db as init_vault_db
from second_brain.scrub_gate import ScrubGateBlocked, gate

log = logging.getLogger(__name__)

SKILL_NAME = "entity-tracking-digest"
_INGEST_METHODS = ("entity-tracking-novel-finding", "entity-tracking-promotion")
_STATE_PATH = Path(config.state_dir()) / "entity_tracking_digest_state.json"


def _load_cursor() -> str:
    import json
    try:
        with open(_STATE_PATH) as f:
            data = json.load(f)
        return data.get("last_digest_at", "1970-01-01T00:00:00+00:00")
    except FileNotFoundError:
        return "1970-01-01T00:00:00+00:00"
    except Exception as e:
        log.warning("entity_tracking_digest: state load failed, starting from epoch: %s", e)
        return "1970-01-01T00:00:00+00:00"


def _save_cursor(now_iso: str) -> None:
    import json
    import os
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{_STATE_PATH}.tmp.{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump({"last_digest_at": now_iso}, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(_STATE_PATH))
    except Exception as e:
        log.error("entity_tracking_digest: state save failed: %s", e)


def _fetch_new_findings(since_iso: str) -> list[dict]:
    conn = sqlite3.connect(INDEX_DB)
    try:
        init_vault_db(conn)
        placeholders = ",".join("?" * len(_INGEST_METHODS))
        rows = conn.execute(
            f"SELECT path, filename, category, tags, ingest_method, indexed_at "
            f"FROM vault_documents "
            f"WHERE ingest_method IN ({placeholders}) AND indexed_at > ? "
            f"ORDER BY ingest_method, path",
            (*_INGEST_METHODS, since_iso),
        ).fetchall()
        return [
            {"path": r[0], "filename": r[1], "category": r[2], "tags": r[3],
             "ingest_method": r[4], "indexed_at": r[5]}
            for r in rows
        ]
    finally:
        conn.close()


def _compose_digest(findings: list[dict], since_iso: str, now_iso: str) -> str:
    novel = [f for f in findings if f["ingest_method"] == "entity-tracking-novel-finding"]
    promoted = [f for f in findings if f["ingest_method"] == "entity-tracking-promotion"]

    def _section(title: str, rows: list[dict]) -> str:
        if not rows:
            return f"## {title}\n\n(none this window)\n"
        lines = "\n".join(
            f"- **{r['filename'].rsplit('.', 1)[0]}** ({r['category'] or 'uncategorized'}) "
            f"-- indexed {r['indexed_at']} -- `{r['path']}`"
            for r in rows
        )
        return f"## {title} ({len(rows)})\n\n{lines}\n"

    return (
        "---\n"
        f"skill: {SKILL_NAME}\n"
        f"window_start: {since_iso}\n"
        f"window_end: {now_iso}\n"
        f"total_findings: {len(findings)}\n"
        "---\n\n"
        f"# Entity-tracking digest: {since_iso} → {now_iso}\n\n"
        "Consolidated view of common.entity_tracking findings written by the "
        "daily-watch skills (aam, aviation, concierge-travel, "
        "executive-protection, gig-economy, trains-yachts) and osint_monitor "
        "since the last digest run. Each entry is a pointer to its own "
        "detail note, not a copy of it -- follow the path for full mention "
        "history and reasoning.\n\n"
        f"{_section('New/updated novel findings (sub-threshold or first-mover, pending review)', novel)}\n"
        f"{_section('Auto-promoted this window', promoted)}"
    )


def run() -> dict:
    status = "ok"
    findings_count = 0
    try:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        since_iso = _load_cursor()

        findings = _fetch_new_findings(since_iso)
        findings_count = len(findings)

        if not findings:
            log.info("entity_tracking_digest: nothing new since %s -- skipping", since_iso)
            return {"status": "skipped", "reason": "no_new_findings", "since": since_iso}

        note = _compose_digest(findings, since_iso, now_iso)
        note = gate(note, source=SKILL_NAME)

        rel_path = f"{webdav_client.BUSINESS_ROOT}/04-Syntheses/entity-tracking/{now.strftime('%Y-%m-%dT%H%M%SZ')}.md"
        webdav_client.put(rel_path, note)

        conn = sqlite3.connect(INDEX_DB)
        try:
            init_vault_db(conn)
            index_note(
                conn, rel_path,
                title=f"Entity-tracking digest ({findings_count} findings)",
                # 2026-08-24: "auto" added -- every sibling automated-skill
                # note (aam-daily-watch, transport-pattern-digest, etc.)
                # carries this tag and it's what the semantic layer's
                # machine_generated concept keys on (observed_as.note_tags).
                # This skill was the one gap found live: 0 of 4 notes had
                # ever been classified as machine-generated/unattended
                # despite genuinely being one, purely because this literal
                # tag was missing.
                content=note, tags="entity-tracking-digest,synthesis,auto",
                ingest_method="entity-tracking-digest",
            )
        finally:
            conn.close()

        _save_cursor(now_iso)
        log.info("entity_tracking_digest: wrote %s (%d findings)", rel_path, findings_count)
        return {"status": "ok", "path": rel_path, "findings": findings_count}

    except ScrubGateBlocked as e:
        status = "error"
        log.error("entity_tracking_digest: scrub gate blocked write: %s", e.reasons)
        return {"status": "error", "reason": "scrub_gate_blocked", "details": e.reasons}
    except Exception as e:
        status = "error"
        log.error("entity_tracking_digest: failed: %s", e)
        return {"status": "error", "reason": str(e)}
    finally:
        log_usage(
            SKILL_NAME, "none", 0, 0,
            status=status if status == "error" else "ok",
            gate_result="new" if findings_count else "skipped",
        )


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run(), indent=2))
