"""
feed_db_integrity_check -- standing check for the exact failure pattern
that let the flight_events/arrival_time gap go unnoticed for ~3 weeks
(root-caused 2026-08-07): a feed reports/logs successful ingest
(feed_state.fetched_at is fresh, error is NULL) but the actual
destination table isn't receiving real writes behind it, OR a specific
field downstream consumers depend on is silently never populated even
though rows ARE landing.

Deliberately distinct from freshness_audit.py, which only checks
feed_state.fetched_at against a staleness threshold -- it never looks at
the destination table at all, which is exactly why it never caught this.
This skill cross-references BOTH:
  1. Silence check: feed_state claims fresh, but the destination table's
     own MAX(timestamp) is stale -- the classic "logs success, DB is
     dark" pattern.
  2. Critical-field check: the destination table IS receiving fresh
     rows, but a specific field every recent row needs (per
     CRITICAL_FIELDS) is null across ALL of them -- the actual shape of
     the flight_events/arrival_time bug, which #1 alone would have
     missed (the table WAS getting writes).

FEED_TABLE_MAP is intentionally a starting, extensible set -- the feeds
most directly relevant to tonight's SWIM/FDPS investigation, not an
exhaustive mapping of all 19 feed_state rows. Add more entries as
they're confirmed rather than guessing a table/column that might be
wrong.

Schedule: every 30 min (corporatetraveldc-feed-db-integrity-check.timer)
-- frequent enough to catch a fresh silent-failure without noise; a
30-week-old bug like the arrival_time one doesn't need faster than this.

SR-1: log_usage() in finally block.
SR-2: Exempt -- deterministic check, no LLM call, inputs always new.
"""
import logging
import sqlite3
import time
from datetime import datetime, timezone

from common import config, db
from common import ntfy_push
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "feed-db-integrity-check"

# feed_state.feed_name -> destination table this feed is actually
# supposed to be populating. ts_type "epoch" = REAL unix timestamp
# column, "iso" = TEXT ISO-8601 column (parsed via strftime comparison).
# silence_threshold_s: how stale the TABLE's own last-write can be
# before it's a mismatch, independent of what feed_state claims.
FEED_TABLE_MAP = {
    "push:fdps": {
        "table": "flight_events", "ts_col": "updated_at", "ts_type": "epoch",
        "silence_threshold_s": 1800,
        # Seeded 2026-08-07 -- the actual confirmed gap. arrival_time has
        # been null on 100% of 579k+ rows since the earliest data
        # (2026-07-20) -- ingest/parsers/fdps_parser.py hardcodes it,
        # FDPS position messages don't carry an ETA natively, and no
        # enrichment (e.g. joining tbfm_sequences.eta by flight_id) was
        # ever built. Kept here as a standing check so this class of gap
        # gets caught fast if it recurs (or a new field-level gap shows
        # up) instead of sitting unnoticed for weeks again.
        "critical_fields": ["arrival_time"],
    },
    "push:tbfm": {
        "table": "tbfm_sequences", "ts_col": "last_seen", "ts_type": "iso",
        "silence_threshold_s": 1800,
    },
    "push:stdds": {
        "table": "stdds_safety_status", "ts_col": "last_seen", "ts_type": "iso",
        "silence_threshold_s": 1800,
    },
    "push:itws": {
        "table": "itws_alerts", "ts_col": "valid_time", "ts_type": "iso",
        "silence_threshold_s": 3600,
    },
    "push:nws": {
        "table": "nws_alerts", "ts_col": "fetched_at", "ts_type": "epoch",
        # 2026-08-07: first live run flagged this at the 3600s (1hr)
        # threshold I'd set for every other feed by default -- turned out
        # to be a false positive. nws_alerts only has 1 row total and the
        # fetcher actively expires old/inactive alerts (expire_nws_alerts
        # in poller/fetchers/nws.py), so "stale" here just means no new
        # SEVERE weather has been issued for the DC area recently, which
        # is normal and can legitimately run many hours quiet -- not a
        # broken parser. Loosened to 24h to stop false-positive noise on
        # this specific feed; genuinely event-driven feeds need their own
        # threshold, not the same default as continuous position-report
        # feeds like FDPS.
        "silence_threshold_s": 86400,
    },
    "push:amtrak": {
        "table": "amtrak_status", "ts_col": "fetched_at", "ts_type": "epoch",
        "silence_threshold_s": 1800,
    },
    # push:fns, push:tfms -- not yet mapped to a confirmed destination
    # table; add when confirmed rather than guessing wrong here.
}


def _table_last_write_age(conn: sqlite3.Connection, table: str, ts_col: str, ts_type: str) -> float | None:
    if ts_type == "epoch":
        row = conn.execute(f"SELECT MAX({ts_col}) FROM {table}").fetchone()
        max_ts = row[0]
        if max_ts is None:
            return None
        return time.time() - max_ts
    else:  # iso
        row = conn.execute(f"SELECT MAX({ts_col}) FROM {table}").fetchone()
        max_iso = row[0]
        if not max_iso:
            return None
        try:
            dt = datetime.fromisoformat(max_iso.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds()
        except ValueError:
            return None


def _critical_field_null_rate(conn: sqlite3.Connection, table: str, ts_col: str, ts_type: str,
                               field: str, lookback_s: int = 3600) -> tuple[int, int] | None:
    """Returns (total_recent_rows, rows_with_field_null) for rows written
    in the last lookback_s, or None if there are no recent rows to check
    (nothing to conclude either way)."""
    if ts_type == "epoch":
        cutoff = time.time() - lookback_s
        total = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {ts_col} > ?", (cutoff,)).fetchone()[0]
        if total == 0:
            return None
        null_count = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {ts_col} > ? AND ({field} IS NULL OR {field} = 0)",
            (cutoff,),
        ).fetchone()[0]
        return total, null_count
    else:
        # ISO-column tables in this map don't currently have a
        # critical_fields entry -- string-timestamp cutoff comparison
        # would need real testing against actual stored formats before
        # trusting it silently. Skipped rather than guessed.
        return None


def run_check() -> list[str]:
    """Returns a list of human-readable mismatch findings, [] if clean.
    Never raises -- a broken check must not itself become an outage."""
    findings = []
    feed_states = {f["feed_name"]: f for f in db.get_feed_states()}
    conn = sqlite3.connect(config.db_path())

    try:
        for feed_name, mapping in FEED_TABLE_MAP.items():
            fs = feed_states.get(feed_name)
            if fs is None:
                continue
            feed_claims_age = (time.time() - fs["fetched_at"]) if fs.get("fetched_at") else None
            feed_healthy = fs.get("error") in (None, "") and feed_claims_age is not None and feed_claims_age < 900

            table_age = _table_last_write_age(conn, mapping["table"], mapping["ts_col"], mapping["ts_type"])
            silence_threshold = mapping["silence_threshold_s"]

            if feed_healthy and (table_age is None or table_age > silence_threshold):
                age_desc = "no rows at all" if table_age is None else f"{table_age/60:.0f}min stale"
                findings.append(
                    f"{feed_name}: feed_state claims healthy (fetched {feed_claims_age/60:.1f}min ago, "
                    f"no error), but {mapping['table']} is {age_desc} -- silent-failure pattern."
                )

            for field in mapping.get("critical_fields", []):
                result = _critical_field_null_rate(conn, mapping["table"], mapping["ts_col"],
                                                    mapping["ts_type"], field)
                if result is None:
                    continue
                total, null_count = result
                if total > 0 and null_count == total:
                    findings.append(
                        f"{feed_name}: {mapping['table']}.{field} is null on ALL {total} rows written "
                        f"in the last hour -- field-level gap, table itself is receiving writes."
                    )
    finally:
        conn.close()

    return findings


def main() -> None:
    status = "ok"
    try:
        findings = run_check()
        if findings:
            status = "mismatch_found"
            body = "\n".join(f"- {f}" for f in findings)
            log.warning("%s: %d mismatch(es) found:\n%s", SKILL_NAME, len(findings), body)
            ntfy_push.send(
                "ops-health",
                f"Feed/DB integrity check found {len(findings)} mismatch(es):\n{body}",
                title="Feed/DB integrity mismatch",
                priority=3, tags="warning",
            )
        else:
            log.info("%s: clean, no mismatches across %d mapped feeds", SKILL_NAME, len(FEED_TABLE_MAP))
    except Exception as e:
        log.error("%s: check itself failed: %s", SKILL_NAME, e)
        status = "error"
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
