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
currently nws (push:nws), notam (push:fns) and, since 2026-09-06, amtrak
(push:amtrak). The SWIM feeds (fdps/stdds/tbfm/itws/tfms) have no REST
twin: no public source carries flight plans, metering or ASDE-X surface
data (OpenSky is ADS-B positions only). Operator decision 2026-09-06:
"keep the three that are hardened, and we'll just take the L on the
others until something comes available." Their outages are the thermal
guard's / durable queue's business, not this script's.

Shed awareness (2026-09-06, work-order item 1): this script reads
thermal-ingest-guard.py's state file. While the guard has the stack shed
(LOCKDOWN, tier 2) the poller itself is stopped, so a forced refresh_feed
trigger has no consumer -- it just piles up in the trigger dir and every
one of them ran back-to-back on the next start (18 NOTAM pulls in a row,
NMS 429, 2026-09-05; the poller now coalesces those, but the right fix is
not writing them at all). And after ANY restore (guard-driven or an
external restart the guard reconciled) the poller has its own startup
hold-off + push grace, so the REST-stale clock is measured from the
restore, not from the last pre-shed fetch: time spent shed never counts
toward the gap threshold.

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

# (poller_feed_name, push_feed_name, poller_interval_seconds, push_max_age)
# Mirrors the push_feed= entries in poller/main.py's FETCH_SCHEDULE --
# keep in sync if that changes. push_max_age matches the poller's gate for
# that feed (FALLBACK_MAX_AGE, or the schedule entry's push_max_age).
WATCHED = (
    ("nws", "nws", 300, 90),
    ("notam", "fns", 300, 90),
    # amtrak-tracker / ingest.amtrak stamp once per 300 s poll, not per 30 s.
    ("amtrak", "amtrak", 300, 660),
)

FALLBACK_MAX_AGE = 90  # seconds -- matches poller/main.py's own constant
# How long past its own interval a REST feed can go before we consider it
# "also not running" -- generous, to avoid firing on ordinary scheduling
# jitter or a single slow cycle. 4x interval = 20 min for a 300s feed.
REST_STALE_MULTIPLIER = 4

# thermal-ingest-guard.py's state file (same path constant as that script;
# duplicated here rather than imported so this backstop has no dependency
# on the guard's module-level side effects).
GUARD_STATE_FILE = "/var/lib/corporatetraveldc/thermal_ingest_guard_state.json"
# Tier at which the poller itself is shed (LOCKDOWN). Tier 1 only stops
# THERMAL_GUARD_TIER1_FEEDS (default tfms,stdds), none of which back a
# WATCHED feed, so the poller is up and a gap at tier 1 is a real gap.
GUARD_LOCKDOWN_TIER = 2
# Post-restore window during which nothing is forced. Default matches the
# guard's own THERMAL_GUARD_RESTORE_DORMANT_S (600 s) and comfortably covers
# the poller's hold-off (120 s) + push grace (120 s) + first re-check
# (300 s) after it starts. dispatch.env is in this unit's EnvironmentFile.
RESTORE_GRACE_S = float(os.environ.get("FAILOVER_GUARDRAIL_RESTORE_GRACE_S", "600"))


def read_guard_state(path: str = GUARD_STATE_FILE) -> dict:
    """Guard state, or {} if absent/unreadable (treated as 'no shed known')."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def shed_reason(guard_state: dict, now: float, restore_grace_s: float) -> str | None:
    """Why forcing a kickover would be wrong right now, or None if it is fine.

    - tier >= LOCKDOWN: the poller is stopped; a trigger would only pile up.
    - restored_at within restore_grace_s: stack is coming back; the poller's
      own hold-off + push grace decide when REST starts. The caller also
      measures rest_age from restored_at, so the shed never counts.
    """
    try:
        tier = int(guard_state.get("tier", 0) or 0)
    except (TypeError, ValueError):
        tier = 0
    if tier >= GUARD_LOCKDOWN_TIER:
        return f"guard tier {tier} (LOCKDOWN shed, poller down)"
    restored_at = guard_state.get("restored_at")
    if isinstance(restored_at, (int, float)) and 0 <= now - restored_at < restore_grace_s:
        return (f"restored {now - restored_at:.0f}s ago via "
                f"{guard_state.get('restored_via', 'guard')} (grace {restore_grace_s:.0f}s)")
    return None


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {LOG_PREFIX} {msg}", flush=True)


def _feed_row(states: dict, name: str) -> dict | None:
    return states.get(name)


def check_one(poller_feed: str, push_feed: str, interval: int,
              states: dict, dry_run: bool, push_max_age: float = FALLBACK_MAX_AGE,
              guard_state: dict | None = None, now: float | None = None) -> bool:
    """Evaluate one feed; returns True if a kickover was (or, dry-run, would
    be) forced. guard_state is thermal-ingest-guard's state dict ({} = none)."""
    push_row = _feed_row(states, f"push:{push_feed}")
    rest_row = _feed_row(states, poller_feed)
    now = time.time() if now is None else now
    guard_state = guard_state if guard_state is not None else {}

    push_healthy = failover.push_is_healthy(push_feed, push_max_age)
    push_age = (now - push_row["fetched_at"]) if push_row and push_row.get("fetched_at") else None
    push_error = push_row.get("error") if push_row else None
    rest_age = (now - rest_row["fetched_at"]) if rest_row and rest_row.get("fetched_at") else None
    # 2026-09-06: time spent shed never counts toward the REST-stale clock.
    # If the guard restored the stack after the last REST run, measure from
    # the restore instead -- the poller could not have run during the shed.
    restored_at = guard_state.get("restored_at")
    if isinstance(restored_at, (int, float)) and restored_at <= now:
        since_restore = now - restored_at
        if rest_age is None or since_restore < rest_age:
            rest_age = since_restore

    log(f"{poller_feed} (push={push_feed}): push_healthy={push_healthy} "
        f"push_age={f'{push_age:.0f}s' if push_age is not None else 'never'} "
        f"push_error={push_error!r} "
        f"rest_age={f'{rest_age:.0f}s' if rest_age is not None else 'never'}")

    if push_healthy:
        return False  # normal: push owns this feed, REST correctly deferring.

    rest_stale_threshold = interval * REST_STALE_MULTIPLIER
    if rest_age is not None and rest_age <= rest_stale_threshold:
        return False  # push is down, but REST is still running fine on its own -- no gap.

    reason = shed_reason(guard_state, now, RESTORE_GRACE_S)
    if reason:
        log(f"{poller_feed}: push unhealthy and REST stale, but NOT forcing -- {reason}")
        return False

    # Gap condition: push is unhealthy AND REST hasn't run recently either.
    rest_age_str = f"{rest_age:.0f}s" if rest_age is not None else "never"
    log(f"*** GAP DETECTED for {poller_feed}: push unhealthy "
        f"(error={push_error!r}, age={push_age}) AND REST stale "
        f"(age={rest_age_str}, threshold={rest_stale_threshold}s). "
        f"{'DRY RUN -- not forcing.' if dry_run else 'Forcing refresh_feed trigger.'}")

    if dry_run:
        return True

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
        f"had NOT fallen back to REST for {rest_age_str} (threshold {rest_stale_threshold}s). "
        f"Forced a refresh_feed trigger manually. This confirms the automatic kickover is "
        f"still broken for an unconfirmed reason -- see CLAUDE.md Known bad, 2026-08-21.",
        f"Failover Guardrail -- forced kickover for {poller_feed}",
        priority=5,
    )
    return True


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
    guard_state = read_guard_state()
    now = time.time()

    if status_only:
        print(json.dumps({"guard_state": guard_state,
                          "shed_reason": shed_reason(guard_state, now, RESTORE_GRACE_S)},
                         indent=2, default=str))
        for poller_feed, push_feed, interval, push_max_age in WATCHED:
            push_row = _feed_row(states, f"push:{push_feed}")
            rest_row = _feed_row(states, poller_feed)
            print(json.dumps({
                "poller_feed": poller_feed,
                "push_feed": push_feed,
                "push_healthy": failover.push_is_healthy(push_feed, push_max_age),
                "push_row": push_row,
                "rest_row": rest_row,
            }, indent=2, default=str))
        return

    reason = shed_reason(guard_state, now, RESTORE_GRACE_S)
    if reason:
        log(f"shed/restore window active -- {reason}; observing only this cycle")
    for poller_feed, push_feed, interval, push_max_age in WATCHED:
        check_one(poller_feed, push_feed, interval, states, dry_run,
                  push_max_age=push_max_age, guard_state=guard_state, now=now)


if __name__ == "__main__":
    main()
