#!/usr/bin/env python3
"""scripts/failover-kickover-guardrail.py

Monitor + active backstop for the poller push/pull failover mechanism
(src/ingest/failover.py, consumed by FetchLoop.maybe_run() in
src/poller/main.py).

Added 2026-08-21 after root-causing (partially) a real incident: NWWS-OI
was broken (SASL not-authorized) continuously from at least 2026-08-18
through a same-day fix to a quoted NWWS_PASSWORD secret, yet the REST
`nws` fetcher -- which exists specifically as a fallback for exactly this
scenario -- never fired a single attempt, successful or failed, the whole
outage. Every individual piece of the failover chain (mark_push_healthy/
mark_push_down in failover.py, push_is_healthy()'s read logic, the DB
upsert, the beat-task cancellation in ingest/nwws.py) was independently
verified correct by direct code reading -- the disconnect between "push
is genuinely down" and "REST fetcher actually runs" was never found.
See CLAUDE.md's Known bad section, 2026-08-21 entry, for the full
investigation trail.

This script does NOT attempt to fix that unconfirmed root cause. It's a
second, independent, deliberately-simple layer that watches the same
signals from outside both processes and forces a kickover if it ever
sees the exact gap condition again -- proving the gap is real (loud
alert) and getting real weather/NOTAM data flowing again regardless of
whatever the underlying bug turns out to be.

Only feeds with a real push-fallback relationship in poller's
FETCH_SCHEDULE are covered (see push_feed= entries in poller/main.py) --
currently nws (push:nws) and notam (push:fns). The SWIM feeds
(fdps/stdds/tbfm/itws/tfms) are ingest-only and covered by
thermal-ingest-guard.py's own shed/restore logic, not this script --
don't add them here.

Usage:
  scripts/failover-kickover-guardrail.py            # normal run (timer)
  scripts/failover-kickover-guardrail.py --status    # print current state, no action
  scripts/failover-kickover-guardrail.py --dry-run   # log what it WOULD force, don't write triggers
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common import config, db  # noqa: E402
from ingest import failover  # noqa: E402

LOG_PREFIX = "failover-guardrail:"

# (poller_feed_name, push_feed_name, poller_interval_seconds)
# Mirrors the push_feed= entries in poller/main.py's FETCH_SCHEDULE --
# keep in sync if that changes.
WATCHED = (
    ("nws", "nws", 300),
    ("notam", "fns", 300),
)

FALLBACK_MAX_AGE = 90  # seconds -- matches poller/main.py's own constant
# How long past its own interval a REST feed can go before we consider it
# "also not running" -- generous, to avoid firing on ordinary scheduling
# jitter or a single slow cycle. 4x interval = 20 min for a 300s feed.
REST_STALE_MULTIPLIER = 4


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {LOG_PREFIX} {msg}", flush=True)


def _feed_row(states: dict, name: str) -> dict | None:
    return states.get(name)


def check_one(poller_feed: str, push_feed: str, interval: int,
              states: dict, dry_run: bool) -> None:
    push_row = _feed_row(states, f"push:{push_feed}")
    rest_row = _feed_row(states, poller_feed)
    now = time.time()

    push_healthy = failover.push_is_healthy(push_feed, FALLBACK_MAX_AGE)
    push_age = (now - push_row["fetched_at"]) if push_row and push_row.get("fetched_at") else None
    push_error = push_row.get("error") if push_row else None
    rest_age = (now - rest_row["fetched_at"]) if rest_row and rest_row.get("fetched_at") else None

    log(f"{poller_feed} (push={push_feed}): push_healthy={push_healthy} "
        f"push_age={f'{push_age:.0f}s' if push_age is not None else 'never'} "
        f"push_error={push_error!r} "
        f"rest_age={f'{rest_age:.0f}s' if rest_age is not None else 'never'}")

    if push_healthy:
        return  # normal: push owns this feed, REST correctly deferring.

    rest_stale_threshold = interval * REST_STALE_MULTIPLIER
    if rest_age is not None and rest_age <= rest_stale_threshold:
        return  # push is down, but REST is still running fine on its own -- no gap.

    # Gap condition: push is unhealthy AND REST hasn't run recently either.
    log(f"*** GAP DETECTED for {poller_feed}: push unhealthy "
        f"(error={push_error!r}, age={push_age}) AND REST stale "
        f"(age={rest_age}, threshold={rest_stale_threshold}s). "
        f"{'DRY RUN -- not forcing.' if dry_run else 'Forcing refresh_feed trigger.'}")

    if dry_run:
        return

    trigger_id = str(uuid.uuid4())
    trigger_dir = Path(config.trigger_dir())
    trigger_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": trigger_id, "type": "refresh_feed",
               "payload": {"feed_name": poller_feed}}
    (trigger_dir / f"{trigger_id}.json").write_text(json.dumps(payload))
    db.insert_trigger(trigger_id, "refresh_feed", {"feed_name": poller_feed})
    log(f"Forced refresh_feed trigger {trigger_id} for {poller_feed}")

    _ntfy_alert(
        f"Failover gap: {poller_feed} (push:{push_feed} down, error={push_error!r}) "
        f"had NOT fallen back to REST for {rest_age:.0f}s (threshold {rest_stale_threshold}s). "
        f"Forced a refresh_feed trigger manually. This confirms the automatic kickover is "
        f"still broken for an unconfirmed reason -- see CLAUDE.md Known bad, 2026-08-21.",
        f"Failover Guardrail -- forced kickover for {poller_feed}",
        priority=5,
    )


def _ntfy_alert(msg: str, title: str, priority: int = 4) -> None:
    try:
        import urllib.request
        env_file = "/etc/corporatetraveldc/dispatch.env"
        secrets_file = "/etc/corporatetraveldc/dispatch-secrets.env"
        base = "http://127.0.0.1:2586"
        topic = "ops-health"
        token = ""
        for f in (env_file,):
            if os.path.exists(f):
                for line in open(f):
                    if line.startswith("NTFY_BASE_URL="):
                        base = line.split("=", 1)[1].strip()
                    if line.startswith("NTFY_OPS_TOPIC="):
                        topic = line.split("=", 1)[1].strip()
        if os.path.exists(secrets_file):
            for line in open(secrets_file):
                if line.startswith("NTFY_TOKEN="):
                    token = line.split("=", 1)[1].strip().split(":")[0]
        req = urllib.request.Request(
            f"{base}/{topic}",
            data=msg.encode(),
            headers={
                "Title": title,
                "Priority": str(priority),
                **({"Authorization": f"Bearer {token}"} if token else {}),
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        log(f"ntfy alert failed (non-fatal): {e}")


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    status_only = "--status" in sys.argv

    states = {s["feed_name"]: s for s in db.get_feed_states()}

    if status_only:
        for poller_feed, push_feed, interval in WATCHED:
            push_row = _feed_row(states, f"push:{push_feed}")
            rest_row = _feed_row(states, poller_feed)
            print(json.dumps({
                "poller_feed": poller_feed,
                "push_feed": push_feed,
                "push_healthy": failover.push_is_healthy(push_feed, FALLBACK_MAX_AGE),
                "push_row": push_row,
                "rest_row": rest_row,
            }, indent=2, default=str))
        return

    for poller_feed, push_feed, interval in WATCHED:
        check_one(poller_feed, push_feed, interval, states, dry_run)


if __name__ == "__main__":
    main()
