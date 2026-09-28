#!/usr/bin/env python3
"""pg-catchup-monitor.py -- Postgres health monitor for SWIM backlog
catch-up bursts.

Purpose (permanent, reusable -- not a one-off script):
  1. Live safety check during any SWIM full-backlog catch-up (a feed
     reconnecting after an outage and replaying more than the default
     2-hour/10%% window -- see SWIM_BACKLOG_STALE_SECONDS and
     SWIM_BACKLOG_RECENT_FRACTION in src/ingest/swim_client.py).
  2. A documented, re-runnable STRESS TEST: point it at a live catch-up
     burst and it produces real numbers on whether Postgres holds up
     under sustained concurrent-write pressure from multiple ingest
     feeds draining backlog simultaneously -- concurrent connection
     count against the max_connections=60 ceiling, per-state breakdown,
     and actual lock contention (pg_locks granted=false), not guesses.
     Run it any time you want evidence the Postgres cutover is a real
     mitigation for the SQLite writer-contention problems it replaced,
     not just a theoretical one.

First real-world use: 2026-09-18 Postgres cutover, SWIM backlog
retention override to keep the full outage window (see dispatch.env's
SWIM_BACKLOG_STALE_SECONDS=28800 / _RECENT_FRACTION=1.0 override, meant
to be reverted once that one-time catch-up is confirmed complete).

What it watches, every --interval seconds for --duration-min minutes:
  - active connection count vs the max_connections=60 ceiling
    (config/postgresql.conf, see POSTGRES_MIGRATION.md 3.2), with a
    warning at 80%% (48 connections)
  - per-state breakdown (active / idle / idle in transaction / ...)
  - blocked queries: pg_locks rows with granted=false -- this is the
    real "is Postgres holding up under concurrent write pressure"
    signal the whole script exists for
  - the longest-running non-idle queries, so a stuck/runaway one is
    visible immediately rather than inferred later from a timeout

What it does NOT watch (pair with these separately):
  - write throughput / rows-per-minute on the ingest side -- that's
    `journalctl -u corporatetraveldc-ingest-*` or the feed's own
    feed_data_usage counters, not this script
  - statement_timeout/lock_timeout kills already happened by the time
    a connection disappears from pg_stat_activity -- this script shows
    state, not history; check the Postgres log for the actual kill
    events if a connection count drops unexpectedly

Usage:
  python3 scripts/pg-catchup-monitor.py [--interval 15] [--duration-min 30]

Run from either the Phase 1 worktree or (once Phase 1 merges to main)
the main checkout -- it imports common.db_backend for the same
_pg_conninfo() helper the migration script and the app itself use, so
connection config always comes from one place.
"""
import argparse
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("NEXTCLOUD_ADMIN_USER", "corporatetraveldc")

import psycopg
from common import db_backend  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=int, default=15,
                     help="seconds between polls (default 15)")
    ap.add_argument("--duration-min", type=int, default=30,
                     help="total minutes to run (default 30)")
    args = ap.parse_args()

    conninfo = db_backend._pg_conninfo()  # noqa: SLF001 -- same reuse migrate-sqlite-to-pg.py does
    conn = psycopg.connect(conninfo, autocommit=True)

    end_time = time.time() + args.duration_min * 60
    print(f"[{datetime.now().isoformat(timespec='seconds')}] pg-catchup-monitor starting, "
          f"{args.duration_min} min window, {args.interval}s interval")

    while time.time() < end_time:
        ts = datetime.now().isoformat(timespec="seconds")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT state, count(*)
                FROM pg_stat_activity
                WHERE datname = current_database()
                GROUP BY state
                ORDER BY count(*) DESC
            """)
            state_counts = cur.fetchall()
            total = sum(c for _, c in state_counts)

            cur.execute("SELECT count(*) FROM pg_locks WHERE NOT granted")
            blocked = cur.fetchone()[0]

            cur.execute("""
                SELECT pid, state, wait_event_type, wait_event,
                       now() - query_start AS running_for, left(query, 80)
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND state != 'idle'
                  AND now() - query_start > interval '10 seconds'
                ORDER BY query_start
                LIMIT 5
            """)
            long_running = cur.fetchall()

        state_str = ", ".join(f"{s or 'unknown'}={c}" for s, c in state_counts)
        pct = 100 * total / 60
        flag = " ⚠ NEAR max_connections=60" if total >= 48 else ""
        print(f"[{ts}] connections={total} ({pct:.0f}% of 60){flag} | {state_str} | blocked_locks={blocked}")
        for pid, state, wtype, wevent, running_for, query in long_running:
            wait_str = f"{wtype}/{wevent}" if wtype else "not waiting"
            print(f"    pid={pid} state={state} running={running_for} wait={wait_str} query={query!r}")

        if blocked > 0:
            print(f"[{ts}] !! {blocked} lock(s) not granted -- real contention, worth a closer look")

        time.sleep(args.interval)

    print(f"[{datetime.now().isoformat(timespec='seconds')}] monitor window complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
