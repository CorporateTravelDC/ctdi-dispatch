"""
nms_v240_post_deploy_check -- one-shot post-deploy verification for the FAA
NMS v2.4.0 production release (window 2026-08-08 0400-0600z). Runs once,
fired by a one-shot systemd timer scheduled ~30min after the window closes.

Diffs current AIXM/FNS NOTAM ingest behavior (push:fns -> aim_parser.py ->
notams table) against the pre-deploy baseline captured by
nms_v240_baseline_capture.py. This does NOT attempt to fully validate the
AIXM 5.1 schema automatically -- that needs a human read of the alert and,
if anything looks off, a manual check against the actual XML payloads and
the FAA's v2.4.0 release notes. What this script CAN do reliably:
  - confirm the feed is still alive at all post-deploy (not silently dead)
  - confirm the insert rate didn't collapse
  - flag any NEW classification values that weren't in the baseline
    (a schema/vocabulary change would likely show up here first)
  - flag any new parse errors/exceptions in the ingest log since baseline

Always pushes a summary to ops-health regardless of outcome (clean or not)
-- this is a one-shot check for a real production deploy, not a routine
timer; the operator should see confirmation either way rather than silence being
the only signal that nothing was checked.

SR-1: not applicable, no Anthropic API call.
SR-2: exempt -- deterministic, one-shot by design.
"""
import json
import logging
import subprocess
import time

from common import config, db, ntfy_push

log = logging.getLogger(__name__)

BASELINE_PATH = "/var/lib/corporatetraveldc/skill-state/nms-v240-baseline.json"


def _recent_parse_error_count(hours: int) -> int:
    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", "corporatetraveldc-ingest",
             "--since", f"{hours} hours ago", "--no-pager"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception as e:
        log.warning("journalctl unavailable for post-deploy check: %s", e)
        return -1
    count = 0
    for line in out.splitlines():
        low = line.lower()
        if ("aim" in low or "notam" in low) and any(
            k in low for k in ("error", "exception", "traceback", "parse")
        ):
            count += 1
    return count


def run_check() -> tuple[str, list[str]]:
    """Returns (status, findings). status in {"clean","attention","no_baseline"}."""
    try:
        with open(BASELINE_PATH) as f:
            baseline = json.load(f)
    except FileNotFoundError:
        return "no_baseline", [
            f"No baseline found at {BASELINE_PATH} -- "
            "nms_v240_baseline_capture.py never ran or was cleaned up. "
            "Cannot diff; here is current state only."
        ]

    import sqlite3
    conn = sqlite3.connect(config.db_path())
    total = conn.execute("SELECT COUNT(*) FROM notams").fetchone()[0]
    last_hour = conn.execute("SELECT COUNT(*) FROM notams WHERE inserted_at > unixepoch()-3600").fetchone()[0]
    classifications = sorted(
        r[0] for r in conn.execute(
            "SELECT DISTINCT classification FROM notams WHERE classification IS NOT NULL"
        ).fetchall()
    )
    latest = conn.execute("SELECT MAX(inserted_at) FROM notams").fetchone()[0]
    conn.close()

    feed_states = {f["feed_name"]: f for f in db.get_feed_states()}
    fns_state = feed_states.get("push:fns", {})
    fns_age_s = (time.time() - fns_state["fetched_at"]) if fns_state.get("fetched_at") else None

    findings = []

    if fns_age_s is None or fns_age_s > 1800:
        findings.append(
            f"push:fns feed_state is stale or missing (age={fns_age_s}) -- "
            "AIXM/FNS NOTAM feed may be dark post-deploy."
        )
    if fns_state.get("error"):
        findings.append(f"push:fns feed_state.error is set post-deploy: {fns_state['error']!r}")

    base_last_hour = baseline["notams_table"]["rows_last_hour"]
    if last_hour == 0 and base_last_hour > 0:
        findings.append(
            f"notams insert rate collapsed to 0/hour post-deploy (baseline was {base_last_hour}/hour)."
        )
    elif base_last_hour > 0 and last_hour < base_last_hour * 0.2:
        findings.append(
            f"notams insert rate dropped sharply: {last_hour}/hour now vs {base_last_hour}/hour baseline "
            "(>80% drop) -- may indicate a parsing/filter regression, not necessarily a real lull."
        )

    base_classes = set(baseline["notams_table"]["distinct_classifications"])
    new_classes = set(classifications) - base_classes
    if new_classes:
        findings.append(
            f"NEW classification value(s) appeared post-deploy that weren't in the baseline: "
            f"{sorted(new_classes)} -- possible schema/vocabulary change in v2.4.0. "
            "aim_parser.py's classification handling should be reviewed against these."
        )

    base_errors = baseline.get("recent_parse_errors_6h", 0)
    post_errors = _recent_parse_error_count(6)
    if post_errors > 0 and post_errors > base_errors:
        findings.append(
            f"New AIM/NOTAM parse error(s) in ingest logs since baseline: {post_errors} in the last 6h "
            f"(baseline had {base_errors}) -- check `journalctl --user -u corporatetraveldc-ingest` for detail."
        )

    status = "clean" if not findings else "attention"

    findings.insert(0,
        f"[context] baseline captured {baseline['captured_at_human']}; "
        f"post-deploy check now: total_rows={total} (was {baseline['notams_table']['total_rows']}), "
        f"last_hour={last_hour} (was {base_last_hour}), "
        f"latest_insert={latest}, push:fns age={fns_age_s}."
    )
    return status, findings


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    status, findings = run_check()
    body = "\n".join(f"- {f}" for f in findings)

    if status == "clean":
        title = "NMS v2.4.0 post-deploy check: clean"
        priority = 3
        tags = "white_check_mark"
    elif status == "no_baseline":
        title = "NMS v2.4.0 post-deploy check: NO BASELINE"
        priority = 4
        tags = "warning"
    else:
        title = "NMS v2.4.0 post-deploy check: NEEDS REVIEW"
        priority = 4
        tags = "warning"

    log.info("%s:\n%s", title, body)
    ntfy_push.send(
        "ops-health",
        f"NMS v2.4.0 (FAA release, deployed 2026-08-08 0400-0600z) post-deploy check -- "
        f"status={status}\n{body}\n\n"
        "This was a one-shot check (not recurring). If status is not 'clean', "
        "review push:fns / notams table / ingest logs manually before trusting NOTAM data.",
        title=title, priority=priority, tags=tags,
    )


if __name__ == "__main__":
    main()
