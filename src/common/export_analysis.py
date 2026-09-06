"""
common.export_analysis -- general pattern for periodic personal-data
export analysis. LinkedIn is the first source built; Uber/Lyft (and
anything added later) plug into the same shared pipeline, not separate
one-off code. 2026-08-07 operator directive, revised same day after
initial design review.

Not real-time -- weekly/monthly manual export drop (you request/download
your own data export from each platform's account settings, then place
it in EXPORT_DROP_DIR), processed on a periodic timer. This is a
fundamentally different, ToS-compliant category from the live-API/
scraping approaches already ruled out for LinkedIn/Instagram/Twitter/
WhatsApp -- it's your own self-service "get a copy of your data" export,
not third-party access to other people's content.

Standing file-handling policy (applies to EVERY export source, not just
LinkedIn -- this is the general contract every per-source parser below
must follow):
  1. The raw file gets a SHA-256 content hash, truncated to 24 hex chars
     -- matches common.sr2_gate's existing hash-content-bearing-fields
     convention exactly, not a new scheme. The hash is for integrity/
     audit -- the raw file itself is never stored or copied elsewhere.
  2. The resulting digest (topic/entity signal only, never raw export
     content) gets persisted to the vault under 04-Syntheses/daily/ --
     not ephemeral. Because second_brain_weekly.py already scans that
     folder (2026-08-06 fix), every digest automatically feeds the
     weekly compile with no extra wiring. second_brain_daily.py gets a
     small addition (see that module) to surface same-day digests in
     its own daily rollup.
  3. Every processing run is logged via common.db.audit() -- the
     platform's existing audit-log table/function, not a new one.
  4. Raw files are read in place from wherever you dropped them, never
     copied or relocated as a side effect of processing -- matches the
     reference LinkedIn skill's (Cowork-side) own established rule.
     Automated cleanup of processed files is a separate, explicit ask
     if wanted later.
  5. Privacy: never echo email addresses, phone numbers, or raw message
     content into any output. Never quote long runs of comment/share
     text verbatim -- reference by topic + date instead. All analysis
     runs locally (already the platform-wide rule via
     allow_anthropic=False everywhere; restated here because this
     module handles more sensitive input than most).

Topic/entity extraction reuses common.entity_tracking.extract_entities()
directly rather than a second bespoke LLM prompt -- export rows get
shaped into the same {title, summary, source} item dicts the RSS
pipeline already uses, so this gets the same signal_type tagging and
justification fields for free, tested code, not a parallel system.
"""
import csv
import hashlib
import logging
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from common import db
from common import entity_tracking

log = logging.getLogger(__name__)

EXPORT_DROP_DIR = Path("/var/lib/corporatetraveldc/personal-exports/incoming")


def file_content_hash(path: Path) -> str:
    """SHA-256 truncated to 24 hex chars -- matches common.sr2_gate's
    existing convention exactly, not a new scheme."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:24]


def record_processed(source_type: str, file_path: Path, digest_summary: str, entities: list[str]) -> str:
    """Shared persistence step for every export source. Hashes the raw
    file (never stores it), persists the digest to the vault, logs via
    the existing audit_log mechanism. Returns the vault rel_path written.
    """
    from second_brain import webdav_client
    from second_brain.index_db import INDEX_DB, index_note
    from second_brain.index_db import init_db as init_vault_db

    file_hash = file_content_hash(file_path)
    today = date.today().isoformat()
    generated_at = datetime.now(timezone.utc).isoformat()

    note = (
        "---\n"
        f"source_type: {source_type}\n"
        f"date: {today}\n"
        f"original_filename: {file_path.name}\n"
        f"file_hash: {file_hash}\n"
        f"generated_at: {generated_at}\n"
        "---\n\n"
        f"# Export Analysis — {source_type} — {today}\n\n"
        f"{digest_summary}\n\n"
        f"**Entities/topics surfaced:** {', '.join(entities) if entities else '(none)'}\n"
    )

    rel_path = f"{webdav_client.BUSINESS_ROOT}/04-Syntheses/daily/export-analysis-{source_type}-{today}.md"
    webdav_client.put(rel_path, note)

    conn = sqlite3.connect(INDEX_DB)
    init_vault_db(conn)
    index_note(
        conn, rel_path, title=f"Export Analysis — {source_type} — {today}", content=note,
        tags=f"export-analysis,{source_type},auto", ingest_method=f"export-analysis-{source_type}",
    )
    conn.close()

    db.audit(
        action="export_analysis_processed",
        tier="system",
        token_prefix=None,
        remote_addr=None,
        detail={
            "source_type": source_type,
            "original_filename": file_path.name,
            "file_hash": file_hash,
            "digest_note_path": rel_path,
            "entity_count": len(entities),
        },
    )
    log.info("export_analysis: processed %s (%s), hash=%s -> %s",
              file_path.name, source_type, file_hash, rel_path)
    return rel_path


# ── LinkedIn-specific parsing ──────────────────────────────────────────
# Reference: the real Cowork-side linkedin-export-analyzer skill's
# established file scope and privacy rules, confirmed 2026-08-07 --
# matched exactly rather than re-derived.

LINKEDIN_INCLUDED_PATTERNS = {
    "shares": re.compile(r"^Shares_\d+\.csv$", re.IGNORECASE),
    "comments": re.compile(r"^Comments_\d+\.csv$", re.IGNORECASE),
    "reactions": re.compile(r"^Reactions_\d+\.csv$", re.IGNORECASE),
}
# Explicitly never opened, even by accident -- messages.csv (full DM
# content), Connections.csv (email/contact fields), SearchQueries.csv,
# Ads Clicked.csv. Not a filter applied to an "everything" scan -- this
# module only ever looks for the three included patterns above, so these
# are never touched rather than touched-then-excluded.


def linkedin_files_in_scope(export_dir: Path) -> dict[str, Path]:
    """Returns {kind: path} for whichever of shares/comments/reactions
    exist in this export. Filename matching is by pattern, never exact
    name -- the real export appends the account's numeric LinkedIn ID."""
    found = {}
    if not export_dir.exists():
        return found
    for f in export_dir.iterdir():
        if not f.is_file():
            continue
        for kind, pattern in LINKEDIN_INCLUDED_PATTERNS.items():
            if pattern.match(f.name):
                found[kind] = f
    return found


def _read_csv_rows(path: Path) -> list[dict]:
    """Best-effort CSV read -- LinkedIn's real column headers weren't
    available to verify against at build time, so this reads the header
    row dynamically rather than hardcoding column names/indices. Should
    be re-verified against a real export on first live run."""
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        log.warning("export_analysis: failed to read %s: %s", path, e)
        return []


def _row_text_and_date(row: dict) -> tuple[str, str]:
    """Heuristic column detection -- looks for a date-like and a
    text-like column by header name rather than a fixed position, since
    exact LinkedIn column names weren't confirmed at build time."""
    text, date_val = "", ""
    for k, v in row.items():
        if not v:
            continue
        kl = (k or "").lower()
        if not date_val and "date" in kl:
            date_val = v
        elif not text and any(w in kl for w in ("commentary", "comment", "message", "text", "content")):
            text = v
    return text, date_val


def extract_linkedin_topics(files: dict[str, Path]) -> tuple[str, list[str]]:
    """Reads shares/comments/reactions CSVs, shapes each row into the
    same {title, summary, source} item dict the RSS pipeline uses, and
    reuses entity_tracking.extract_entities() for the actual topic/
    entity extraction -- same tested LLM pipeline, no second prompt.
    Returns (digest_summary_text, entity_list). Never quotes row text
    verbatim in the digest -- only topic + date, per the privacy policy
    above."""
    items = []
    for kind, path in files.items():
        for row in _read_csv_rows(path):
            text, date_val = _row_text_and_date(row)
            if not text:
                continue
            items.append({
                "title": text[:200],
                "summary": "",
                "source": f"linkedin_export_{kind}",
                "published": date_val,
            })

    if not items:
        return "No shares/comments/reactions content found in this export.", []

    hits = entity_tracking.extract_entities(items)
    if not hits:
        return f"Processed {len(items)} export rows; no distinct topics/entities surfaced.", []

    entity_names = sorted(hits.keys())
    lines = [f"Processed {len(items)} export rows across {len(files)} file(s)."]
    for name, hit in hits.items():
        # Reference by topic, not verbatim text -- item_indices map back
        # to the numbered items list, dates come from the source rows.
        sample_dates = sorted({items[i - 1].get("published", "") for i in hit["indices"] if items[i - 1].get("published")})
        date_note = f" (seen: {', '.join(sample_dates[:3])})" if sample_dates else ""
        lines.append(f"- {name}: {hit['signal_type']}{date_note} -- {hit['justification']}")

    return "\n".join(lines), entity_names


def process_linkedin_export(export_dir: Path = EXPORT_DROP_DIR) -> list[str]:
    """Top-level entry point for a periodic LinkedIn export-processing
    run. Non-fatal by construction -- a broken export or extraction
    failure logs and returns [], never raises. Returns the list of vault
    rel_paths written this run (0 or 1 -- one combined digest per run,
    not per file)."""
    try:
        files = linkedin_files_in_scope(export_dir)
        if not files:
            log.info("export_analysis: no LinkedIn export files found in %s", export_dir)
            return []
        digest, entities = extract_linkedin_topics(files)
        # One representative file for the content-hash record -- hashing
        # every included file individually would also be reasonable;
        # this keeps the audit record simple for a first pass.
        representative = next(iter(files.values()))
        rel_path = record_processed("linkedin", representative, digest, entities)
        return [rel_path]
    except Exception as e:
        log.warning("export_analysis: LinkedIn processing failed: %s", e)
        return []
