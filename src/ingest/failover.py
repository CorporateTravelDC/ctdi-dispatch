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
# 2026-09-06: "amtrak" added -- push:amtrak is stamped by amtrak-tracker /
# ingest.amtrak once per successful poll (every ~300 s, NOT every 30 s like
# the SWIM/NWWS heartbeats), so its poller gate uses a per-feed max_age.
PUSH_FEEDS = ("fdps", "stdds", "fns", "tbfm", "tfms", "itws", "nws", "amtrak")

# Poller-side gate default: a push heartbeat fresher than this means push
# owns the data and the REST twin stays idle. Must exceed the 30 s
# SWIM/NWWS heartbeat interval with margin to avoid flapping.
FALLBACK_MAX_AGE = 90  # seconds

# 2026-09-07: THE single source of truth for "which REST fetcher has a push
# twin". Keyed by the poller's FETCH_SCHEDULE name (also the REST feed's
# feed_state row); value = the push feed name (heartbeat row "push:<name>")
# plus an optional per-feed gate for twins that stamp per poll rather than
# every 30 s. Read by poller/main.py (FETCH_SCHEDULE push gate), web/main.py
# (/healthz + /api/v1/feeds push_covers / stale_thresholds) and the tests
# that pin the kickover guardrail's WATCHED table to the schedule. Every
# push feed in PUSH_FEEDS that is NOT listed here has no REST twin by
# operator decision (fdps/stdds/tbfm/tfms/itws -- see
# docs/REST_FALLBACK_AUDIT_2026-09-06.md section 5). tfr and nas
# deliberately have no push twin (2026-07-23).
REST_FALLBACKS: dict[str, dict] = {
    "nws":    {"push_feed": "nws"},
    "notam":  {"push_feed": "fns"},
    "amtrak": {"push_feed": "amtrak", "push_max_age": 660},
}

# Status-page floor for a push heartbeat row (web stale_thresholds and the
# push_covered window). The web never suppresses a poll, so it tolerates
# more than the poller's 90 s gate -- one missed guard sample must not
# flash "stale" -- but never LESS than the poller's gate for that twin.
PUSH_STALE_FLOOR_S = 300


def _key(feed: str) -> str:
    return f"{PREFIX}{feed}"


def push_max_age(rest_feed: str) -> float:
    """The poller's gate for <rest_feed>'s push twin (FALLBACK_MAX_AGE
    unless REST_FALLBACKS overrides it). KeyError if there is no twin."""
    return REST_FALLBACKS[rest_feed].get("push_max_age", FALLBACK_MAX_AGE)


def push_covers() -> dict[str, str]:
    """{rest_feed: "push:<feed>"} for every REST fetcher with a push twin."""
    return {rest: _key(spec["push_feed"]) for rest, spec in REST_FALLBACKS.items()}


def cover_max_age(rest_feed: str) -> float:
    """Status-page window in which a push heartbeat counts as covering
    <rest_feed>: max(PUSH_STALE_FLOOR_S, the poller's gate)."""
    return max(PUSH_STALE_FLOOR_S, push_max_age(rest_feed))


def push_stale_thresholds() -> dict[str, float]:
    """{"push:<feed>": seconds} for EVERY push feed: the floor for feeds
    without a REST twin, cover_max_age() for feeds with one."""
    out = {_key(feed): float(PUSH_STALE_FLOOR_S) for feed in PUSH_FEEDS}
    for rest, spec in REST_FALLBACKS.items():
        out[_key(spec["push_feed"])] = float(cover_max_age(rest))
    return out


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


def push_last_seen(feed: str) -> float | None:
    """Epoch of <feed>'s last push heartbeat stamp, error or not; None if the
    row has never been written or the DB is unreadable.

    2026-09-06: lets the poller tell a heartbeat that went stale BEFORE its
    own process started (a LOCKDOWN shed / restart -- the push feed is
    probably still coming back up) apart from one that went stale while
    the poller was already running (the push feed really died). See
    FetchLoop.maybe_run() in poller/main.py.
    """
    try:
        for s in db.get_feed_states():
            if s.get("feed_name") == _key(feed):
                return s.get("fetched_at") or None
    except Exception:
        return None
    return None
