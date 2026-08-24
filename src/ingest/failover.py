"""
ingest.failover — the coordination point between push (ingest) and poll (poller).

Each push source, while its connection is alive, periodically calls
mark_push_healthy(feed). That stamps a row in feed_state named "push:<feed>".

The poller, before running a fetcher that has a push twin, calls
push_is_healthy(feed, max_age). If the heartbeat is fresh, the poller SKIPS its
REST fetch (push owns the data). If the heartbeat is stale — connection dropped,
ingest crashed, container down — the poller resumes polling as the fallback.

Key point: the heartbeat reflects CONNECTION health, not data arrival. SWIM is
event-driven, so quiet periods (no new TFRs) are normal and must not trip the
fallback. Sources heartbeat on a timer while connected, regardless of traffic.

This uses only the existing db API (upsert_feed / get_feed_states) — no schema
change. The "push:" prefix namespaces these rows away from the poller's own
feed_state entries.
"""
from __future__ import annotations

import time

from common import db

PREFIX = "push:"

# Feeds that have BOTH a push source (here) and a poll fallback (poller fetcher).
# metar / runsheet / atcscc have no push twin and always poll.
PUSH_FEEDS = ("fdps", "stdds", "fns", "tbfm", "tfms", "itws", "nws")


def _key(feed: str) -> str:
    return f"{PREFIX}{feed}"


def mark_push_healthy(feed: str, error: str | None = None) -> None:
    """Stamp a heartbeat for a push source. Call on a timer while connected."""
    db.upsert_feed(_key(feed), time.time(), error)


def mark_push_down(feed: str, error: str) -> None:
    """Record that a push source's connection is down (records the error and
    leaves fetched_at as the last-known time so it ages out)."""
    # We deliberately do NOT bump fetched_at here — letting the timestamp age is
    # what trips the poller fallback. We only annotate the error for visibility.
    states = {s["feed_name"]: s for s in db.get_feed_states()}
    prev = states.get(_key(feed))
    last = prev.get("fetched_at") if prev else 0.0
    db.upsert_feed(_key(feed), last or 0.0, error)


def push_is_healthy(feed: str, max_age: float) -> bool:
    """True if <feed>'s push heartbeat is fresher than max_age seconds.

    Called by the poller. If True, the poller skips its REST fetch for <feed>.
    Defaults to False (poll) on any uncertainty — fail safe toward polling.
    """
    try:
        for s in db.get_feed_states():
            if s.get("feed_name") == _key(feed):
                ts = s.get("fetched_at") or 0.0
                if s.get("error"):
                    return False
                return (time.time() - ts) <= max_age
    except Exception:
        return False
    return False
