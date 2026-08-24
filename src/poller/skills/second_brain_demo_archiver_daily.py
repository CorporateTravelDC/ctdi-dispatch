"""
second_brain_demo_archiver_daily -- daily rolling ingest of raw operational
history from the demo-archiver recorder into the second brain.

Operator directive 2026-07-23: "let's make a daily timer that actually
scrapes the last, say, 24 to 36 hours of data from the demo archiver and
dumps that into a rolling ingest in second brain (so that it has more data
than just the ops process plan and the [operator LLC abbreviation]s' predictive and retroactive
look backs on the briefs)."

Source: reads DIRECTLY from /var/lib/corporatetraveldc/demo.db (the
recorder's raw snapshot table), NOT the demo-api playback service on
:8004. This is a deliberate distinction -- the playback API is designed
to never reflect real/current data (privacy-safe looping replay off a
fixed anchor, see demo_archiver_linkage_done memory / demo_api.py's own
docstring). This skill wants the opposite: genuine recent operational
history. Reading the recorder's SQLite table directly is correct and
safe here -- it's the same box, same user, no new exposure, and it's
exactly the data source second_brain_daily.py already draws its own
brief content from one layer up.

Format decision: the recorder dedups on payload hash at write time, so
rows already represent real changes, not every 5-min poll -- but a 24-36h
window is still ~1,100 rows across 7-9 endpoints (2026-07-23 sample: 354
amtrak, 322 notams, 178 weather, 168 route, 33 opsplan, 32 cps, 21
brief). Dumping every row's full payload would make the daily note
enormous and mostly redundant (amtrak/notams flutter in small status
deltas). Instead, per endpoint: change count + first/last capture time
+ the single latest full payload (current state) as the representative
snapshot. This gives real breadth (raw feed data, not just brief
narrative) while keeping the note a bounded, readable size. If finer
granularity turns out to matter (e.g. wanting every distinct TFR change,
not just the latest), that's a follow-up tuning pass, not a rebuild.

SR-1: log_usage() in finally block.
SR-2: Exempt -- time-bounded input (rolling window), inputs always new.
"""
import json
import logging
import pathlib
import sqlite3
import zlib
from datetime import date, datetime, timedelta, timezone

from common import config
from common.sr1_log import log_usage
from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
from second_brain.scrub_gate import ScrubGateBlocked, gate

log = logging.getLogger(__name__)

SKILL_NAME = "demo-archiver-daily"
DEMO_DB = "/var/lib/corporatetraveldc/demo.db"
LOOKBACK_HOURS = 30  # midpoint of operator's "24 to 36 hours" ask

# Cap how much of a single endpoint's latest payload gets embedded --
# some payloads (notams, route) can run long; keep the note readable
# and flag truncation rather than silently cutting content.
_MAX_PAYLOAD_CHARS = 2000


def _fetch_window() -> dict[str, list[tuple[str, bytes, int]]]:
    """Query the recorder DB directly for rows in the lookback window.
    Returns {endpoint: [(captured_at, payload, compressed), ...]} ordered
    oldest-to-newest per endpoint. Read-only connection -- this skill
    never writes to demo.db, only to the vault."""
    conn = sqlite3.connect(f"file:{DEMO_DB}?mode=ro", uri=True)
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).isoformat()
        rows = conn.execute(
            "SELECT endpoint, captured_at, payload, compressed FROM snapshots "
            "WHERE captured_at >= ? ORDER BY endpoint, captured_at ASC",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    by_endpoint: dict[str, list[tuple[str, bytes, int]]] = {}
    for endpoint, captured_at, payload, compressed in rows:
        by_endpoint.setdefault(endpoint, []).append((captured_at, payload, compressed))
    return by_endpoint


def _decode(payload: bytes, compressed: int) -> str:
    raw = zlib.decompress(payload) if compressed else payload
    try:
        # Pretty-print if it's JSON -- more readable in the note than a
        # single dense line, and still round-trips fine either way.
        return json.dumps(json.loads(raw), indent=2, sort_keys=True)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def _render_endpoint_section(endpoint: str, rows: list[tuple[str, bytes, int]]) -> str:
    first_at = rows[0][0]
    last_at = rows[-1][0]
    change_count = len(rows)
    latest_captured_at, latest_payload, latest_compressed = rows[-1]

    try:
        latest_text = _decode(latest_payload, latest_compressed)
    except Exception as e:
        latest_text = f"(payload decode failed: {e})"

    truncated_note = ""
    if len(latest_text) > _MAX_PAYLOAD_CHARS:
        latest_text = latest_text[:_MAX_PAYLOAD_CHARS]
        truncated_note = f"\n... [truncated, {change_count} total changes this window]"

    return (
        f"## {endpoint}\n\n"
        f"Changes in window: {change_count}  "
        f"(first: {first_at}, last: {last_at})\n\n"
        f"Latest snapshot (as of {latest_captured_at}):\n\n"
        f"```json\n{latest_text}{truncated_note}\n```\n"
    )


def main() -> None:
    status = "error"
    today = date.today()

    try:
        by_endpoint = _fetch_window()
        if not by_endpoint:
            log.info("%s: no snapshots in the last %dh window -- recorder may be down, "
                      "skipping vault write", SKILL_NAME, LOOKBACK_HOURS)
            status = "empty"
            return

        total_rows = sum(len(v) for v in by_endpoint.values())
        sections = [
            _render_endpoint_section(ep, rows)
            for ep, rows in sorted(by_endpoint.items())
        ]
        body = (
            f"Rolling {LOOKBACK_HOURS}h ingest from the demo-archiver recorder "
            f"({total_rows} total changed snapshots across {len(by_endpoint)} endpoints). "
            "Raw operational feed history, not brief narrative -- see module "
            "docstring for why this reads demo.db directly rather than the "
            "demo-api playback service.\n\n"
            + "\n".join(sections)
        )

        gated = gate(body, source=f"{SKILL_NAME}")
        status = "ok"

        generated_at = datetime.now(timezone.utc).isoformat()
        header = f"DEMO-ARCHIVER DAILY INGEST -- {today.isoformat()} (generated {generated_at})\n\n"
        full_text = header + gated.strip() + "\n"

        frontmatter = (
            "---\n"
            f"date: {today.isoformat()}\n"
            "ingest_method: demo-archiver-daily\n"
            f"generated_at: {generated_at}\n"
            f"endpoints: {len(by_endpoint)}\n"
            f"total_changes: {total_rows}\n"
            "---\n\n"
        )
        note = frontmatter + full_text

        rel_path = f"{webdav_client.BUSINESS_ROOT}/01-Sources/demo-archive/{today.isoformat()}.md"
        webdav_client.put(rel_path, note)

        conn = sqlite3.connect(INDEX_DB)
        init_vault_db(conn)
        index_note(
            conn, rel_path, title=f"Demo-archiver ingest — {today.isoformat()}",
            content=note, tags="daily,demo-archiver,raw-feed,auto",
            ingest_method="demo-archiver-daily",
        )
        conn.close()
        log.info("%s: wrote %s (%d endpoints, %d total changes, status=%s)",
                  SKILL_NAME, rel_path, len(by_endpoint), total_rows, status)

    except ScrubGateBlocked as e:
        status = "blocked"
        log.error("%s: BLOCKED by scrub gate: %s", SKILL_NAME, e)
    except Exception as e:
        status = "error"
        log.error("%s: failed: %s", SKILL_NAME, e)
        raise
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
