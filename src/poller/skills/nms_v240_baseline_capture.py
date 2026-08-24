"""
nms_v240_baseline_capture -- one-shot pre-deploy snapshot for the FAA NMS
(NOTAM Management Services) v2.4.0 production release (deploying
2026-08-08 0400-0600z per FAA notice, facility DCC, RMLS ID 1466577532).

Our live NOTAM ingest path is push:fns -> ingest/parsers/aim_parser.py
(AIXM 5.1 AIXMBasicMessage over Solace AMQP) -> notams table -- confirmed
live and healthy 2026-08-07 (5,697 rows total, 24 in the last hour,
zero parse errors in the last 6h of ingest logs). This is a DIFFERENT
path from the poller/fetchers/notam.py REST fetcher, which is dead
(feed_state shows "awaiting_credentials" -- FAA_NOTAM_API_KEY was never
provisioned). NMS v2.4.0 is a risk to the AIXM/FNS push path, not the
REST fetcher.

The FAA notice claims no impact to origination/distribution during the
maintenance window, but a version bump can still change response
schema/behavior afterward -- this is exactly the class of assumption
that shouldn't be trusted without checking. This script captures a
"before" snapshot; nms_v240_post_deploy_check.py (run once via a
one-shot timer after 0600z) diffs against it.

SR-1: not applicable, no Anthropic API call.
SR-2: exempt -- deterministic, one-shot by design (not a recurring skill).
"""
import json
import logging
import subprocess
import time

from common import config, db

log = logging.getLogger(__name__)

BASELINE_PATH = "/var/lib/corporatetraveldc/skill-state/nms-v240-baseline.json"


def _recent_parse_error_count(hours: int = 6) -> int:
    """journalctl count of aim/notam-related error/exception/traceback lines
    from the ingest service in the last N hours. Best-effort -- returns -1
    if journalctl isn't available rather than a false 0."""
    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", "corporatetraveldc-ingest",
             "--since", f"{hours} hours ago", "--no-pager"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception as e:
        log.warning("journalctl unavailable for baseline capture: %s", e)
        return -1
    count = 0
    for line in out.splitlines():
        low = line.lower()
        if ("aim" in low or "notam" in low) and any(
            k in low for k in ("error", "exception", "traceback", "parse")
        ):
            count += 1
    return count


def capture() -> dict:
    conn_row = lambda sql: __import__("sqlite3").connect(config.db_path()).execute(sql).fetchone()

    total = conn_row("SELECT COUNT(*) FROM notams")[0]
    last_hour = conn_row("SELECT COUNT(*) FROM notams WHERE inserted_at > unixepoch()-3600")[0]
    classifications = [
        r[0] for r in __import__("sqlite3").connect(config.db_path())
        .execute("SELECT DISTINCT classification FROM notams WHERE classification IS NOT NULL").fetchall()
    ]
    latest = conn_row("SELECT MAX(inserted_at) FROM notams")[0]

    feed_states = {f["feed_name"]: f for f in db.get_feed_states()}
    fns_state = feed_states.get("push:fns", {})

    baseline = {
        "captured_at": time.time(),
        "captured_at_human": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "purpose": "NMS v2.4.0 pre-deploy baseline (FAA maint window 2026-08-08 0400-0600z)",
        "notams_table": {
            "total_rows": total,
            "rows_last_hour": last_hour,
            "distinct_classifications": sorted(classifications),
            "latest_inserted_at": latest,
        },
        "push_fns_feed_state": {
            "fetched_at": fns_state.get("fetched_at"),
            "error": fns_state.get("error"),
            "consecutive_failures": fns_state.get("consecutive_failures"),
        },
        "recent_parse_errors_6h": _recent_parse_error_count(6),
    }
    return baseline


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    baseline = capture()
    with open(BASELINE_PATH, "w") as f:
        json.dump(baseline, f, indent=2)
    log.info("NMS v2.4.0 baseline captured -> %s\n%s", BASELINE_PATH, json.dumps(baseline, indent=2))


if __name__ == "__main__":
    main()
