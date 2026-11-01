"""Bounded summaries of cleared content in the isolated promoted demo store.

No raw production snapshot accessor and no fallback. Both scrubbed snapshots
and synthetic replay fill are eligible, bounded by the completed promotion.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from common import db_backend


MAX_STORED_PAYLOAD_BYTES = 131072


class PromotionUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class WindowSummary:
    endpoint: str
    change_count: int
    first_at: str
    last_at: str
    payload: bytes
    compressed: bool
    synthetic_count: int = 0


@dataclass(frozen=True)
class PromotionWindow:
    window_start: str
    window_end: str
    promoted_at: str
    promotion_seq: int
    promotion_hash: str
    summaries: list[WindowSummary]


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise PromotionUnavailable("Invalid promotion metadata; no raw-data fallback") from None


def fetch_window_summary(lookback_hours: int = 30) -> PromotionWindow:
    if not 1 <= lookback_hours <= 168:
        raise ValueError("Archive window must be between 1 and 168 hours")
    with db_backend.demo_pg_conn() as conn:
        # Metadata and rows must describe one snapshot, not a moving promotion.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        meta = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM meta WHERE key IN ('window_end', 'promoted_at')"
        ).fetchall()}
        receipt = conn.execute(
            "SELECT seq, run_at, window_end, entry_hash FROM scrub_ledger ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if not receipt or not meta.get("window_end") or not meta.get("promoted_at"):
            raise PromotionUnavailable("Completed demo promotion missing; no raw-data fallback")
        end = _utc(meta["window_end"])
        promoted = _utc(meta["promoted_at"])
        if (end != _utc(receipt["window_end"]) or promoted != _utc(receipt["run_at"])
                or end > promoted
                or not re.fullmatch(r"[0-9a-f]{64}", str(receipt["entry_hash"]))):
            raise PromotionUnavailable("Demo promotion incomplete or inconsistent; no raw-data fallback")
        # The scrub stores UTC capture strings. Anchor to its promoted boundary,
        # never wall-clock now (which would make old demo windows look empty).
        start = (end - timedelta(hours=lookback_hours)).replace(tzinfo=None).isoformat(timespec="seconds")
        boundary = meta["window_end"]
        rows = conn.execute("""
            WITH window_stats AS (
                SELECT endpoint, COUNT(*) AS change_count,
                       MIN(captured_at) AS first_at, MAX(captured_at) AS last_at,
                       SUM(CASE WHEN synthetic THEN 1 ELSE 0 END) AS synthetic_count
                FROM snapshots WHERE captured_at >= ? AND captured_at <= ?
                GROUP BY endpoint
            )
            SELECT w.endpoint, w.change_count, w.first_at, w.last_at,
                   w.synthetic_count, SUBSTR(s.payload, 1, ?) AS payload, s.compressed
            FROM window_stats w JOIN snapshots s ON s.id = (
                SELECT latest.id FROM snapshots latest
                WHERE latest.endpoint = w.endpoint
                  AND latest.captured_at >= ? AND latest.captured_at <= ?
                ORDER BY latest.captured_at DESC, latest.synthetic ASC, latest.id DESC LIMIT 1
            ) ORDER BY w.endpoint
        """, (start, boundary, MAX_STORED_PAYLOAD_BYTES, start, boundary)).fetchall()
    return PromotionWindow(start, end.isoformat(), promoted.isoformat(), int(receipt["seq"]),
                           str(receipt["entry_hash"]), [WindowSummary(
                               r["endpoint"], r["change_count"], r["first_at"], r["last_at"],
                               bytes(r["payload"]), bool(r["compressed"]), r["synthetic_count"]
                           ) for r in rows])
