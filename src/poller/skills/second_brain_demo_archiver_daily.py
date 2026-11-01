"""Archive only cleared data already promoted into the isolated demo store.

Operator clarification 2026-10-29: include both scrubbed promoted snapshots and
synthetic replay fill. Never read raw production demo_snapshots or fall back
to it. The input window is the last 30 hours of completed promoted history,
not the last 30 hours of live operations. The output is explicitly demo-only.
Memory stays bounded: counts plus one capped representative payload per endpoint.
"""
import json
import logging
import zlib
from datetime import datetime, timezone

from common import optime
from common.sr1_log import log_usage
from demo import promoted_archive as demo_db
from second_brain import webdav_client
from second_brain.index_db import get_conn, index_note
from second_brain.scrub_gate import ScrubGateBlocked, gate

log = logging.getLogger(__name__)

SKILL_NAME = "demo-archiver-daily"
LOOKBACK_HOURS = 30  # midpoint of operator's "24 to 36 hours" ask

# Cap how much of a single endpoint's latest payload gets embedded --
# some payloads (notams, route) can run long; keep the note readable
# and flag truncation rather than silently cutting content.
_MAX_PAYLOAD_CHARS = 2000


def _fetch_window() -> demo_db.PromotionWindow:
    """Read a completed promoted-demo window; no production-data fallback."""
    return demo_db.fetch_window_summary(LOOKBACK_HOURS)


# Bound decompression before JSON parsing, not only the final rendered note.
# Very large snapshots still contribute their counts and a labelled prefix.
_MAX_DECODE_BYTES = 65536


def _decode(payload: bytes, compressed: bool) -> str:
    if compressed:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(payload, _MAX_DECODE_BYTES + 1)
        truncated = len(raw) > _MAX_DECODE_BYTES
        if not truncated and not decoder.eof:
            raise zlib.error("incomplete compressed payload")
    else:
        raw = payload[:_MAX_DECODE_BYTES + 1]
        truncated = len(raw) > _MAX_DECODE_BYTES
    if truncated:
        return ("[truncated before JSON formatting: payload exceeds "
                f"{_MAX_DECODE_BYTES} decoded bytes]\n" +
                raw[:_MAX_DECODE_BYTES].decode("utf-8", errors="replace"))
    try:
        return json.dumps(json.loads(raw), indent=2, sort_keys=True)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def _render_endpoint_section(summary: demo_db.WindowSummary) -> str:
    try:
        latest_text = _decode(summary.payload, summary.compressed)
    except Exception as e:
        latest_text = f"(payload decode failed: {type(e).__name__})"

    truncated_note = ""
    if len(latest_text) > _MAX_PAYLOAD_CHARS:
        latest_text = latest_text[:_MAX_PAYLOAD_CHARS]
        truncated_note = f"\n... [truncated, {summary.change_count} total changes this window]"

    return (
        f"## {summary.endpoint}\n\n"
        f"Changes in window: {summary.change_count} (synthetic replay fill: {summary.synthetic_count})  "
        f"(first: {summary.first_at}, last: {summary.last_at})\n\n"
        f"Latest snapshot (as of {summary.last_at}):\n\n"
        f"```json\n{latest_text}{truncated_note}\n```\n"
    )


def main() -> None:
    status = "error"
    # 2026-10-05 (#9 follow-up): the OPERATIONAL day (America/New_York, 05:00
    # rollover), not the container's UTC calendar date
    today = optime.op_today()

    try:
        promotion = _fetch_window()
        by_endpoint = promotion.summaries
        if not by_endpoint:
            log.info("%s: no cleared snapshots in the promoted %dh window, "
                      "skipping vault write", SKILL_NAME, LOOKBACK_HOURS)
            status = "empty"
            return

        total_rows = sum(summary.change_count for summary in by_endpoint)
        sections = [
            _render_endpoint_section(summary)
            for summary in sorted(by_endpoint, key=lambda summary: summary.endpoint)
        ]
        body = (
            f"Cleared promoted demo history ({total_rows} snapshots across "
            f"{len(by_endpoint)} endpoints, including synthetic replay fill). "
            "Demonstration data only; not evidence of live operations. "
            f"Window: {promotion.window_start} through {promotion.window_end}; "
            f"promotion sequence {promotion.promotion_seq}. "
            "Source: isolated demo snapshots/meta/scrub_ledger, never raw "
            "production recorder snapshots.\n\n"
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
            "data_class: cleared_promoted_demo\n"
            "authority: demonstration_only\n"
            f"promotion_seq: {promotion.promotion_seq}\n"
            f"promotion_hash: {promotion.promotion_hash}\n"
            f"promoted_at: {promotion.promoted_at}\n"
            f"generated_at: {generated_at}\n"
            f"endpoints: {len(by_endpoint)}\n"
            f"total_changes: {total_rows}\n"
            "---\n\n"
        )
        note = frontmatter + full_text

        rel_path = f"{webdav_client.BUSINESS_ROOT}/01-Sources/demo-archive/{today.isoformat()}.md"
        webdav_client.put(rel_path, note)

        with get_conn() as conn:
            index_note(
                conn, rel_path, title=f"Demo-archiver ingest — {today.isoformat()}",
                content=note, tags="daily,demo-archiver,promoted-demo,demonstration-only,auto",
                ingest_method="demo-archiver-daily",
            )
        log.info("%s: wrote %s (%d endpoints, %d total changes, status=%s)",
                  SKILL_NAME, rel_path, len(by_endpoint), total_rows, status)

    except ScrubGateBlocked as e:
        status = "blocked"
        log.error("%s: BLOCKED by scrub gate: %s", SKILL_NAME, type(e).__name__)
        raise
    except Exception as e:
        status = "error"
        log.error("%s: failed: %s", SKILL_NAME, type(e).__name__)
        raise
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
