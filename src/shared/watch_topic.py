"""Push topic for a watch entry (2026-10-20, operator: bellwether set).

Every push keyed to a watch goes to its entry type's own topic (flight-alerts,
train-alerts, vessel-alerts, drone-alerts) plus a concise copy on `dispatch` --
except an entry whose PERMANENT record sets "alert_topic" (watchlists/
permanent_flights.json; "bellwether-alerts" on BA293 and the bellwether set).
Those go to that topic only: no `dispatch` duplicate (NO_DISPATCH_TOPICS).

Source of the record, in order (the shared.vip_listed pattern):
  1. the file itself, read the way shared.watchlist.WatchlistFileWatcher reads it,
     matched by entry id (re-read when its mtime changes);
  2. when the file is not mounted in this container (ingest and pusher have no
     watchlists volume), the watchlist_entries row's alert_topic column, which the
     poller's file watcher copies from the file (pg_schema/0082), if its tier is
     "permanent".
A transient watch never inherits a permanent entry's topic. Nothing here logs or
returns an identifier.
"""
from __future__ import annotations

from shared.vip_listed import _file_records

TYPE_TOPICS = {"flight": "flight-alerts", "train": "train-alerts",
               "vessel": "vessel-alerts", "drone": "drone-alerts"}
DEFAULT_TOPIC = "train-alerts"          # the historical else-branch of watchlist_event_hit
BELLWETHER_TOPIC = "bellwether-alerts"
# Topics whose watch pushes are never duplicated onto `dispatch`.
NO_DISPATCH_TOPICS = frozenset({BELLWETHER_TOPIC})
# Values an alert_topic may take; anything else is ignored (a typo in the file must
# not route a push to a topic nobody subscribes to).
ALLOWED_ALERT_TOPICS = frozenset({BELLWETHER_TOPIC})


def _record_topic(rec: dict | None) -> str | None:
    t = str((rec or {}).get("alert_topic") or "").strip()
    return t if t in ALLOWED_ALERT_TOPICS else None


def alert_topic_override(entry: dict | None) -> str | None:
    """The permanent record's alert_topic for this watch entry, or None."""
    if not entry:
        return None
    recs = _file_records()
    if recs:
        rec = recs.get(entry.get("id"))
        if rec is not None:
            return _record_topic(rec)
        return None
    if entry.get("tier") == "permanent":
        return _record_topic(entry)
    return None


def topic_for_entry(entry: dict | None, entry_type: str | None = None) -> str:
    """The domain topic for a push about this watch entry."""
    override = alert_topic_override(entry)
    if override:
        return override
    etype = entry_type or (entry or {}).get("entry_type") or ""
    return TYPE_TOPICS.get(etype, DEFAULT_TOPIC)


def is_bellwether(entry: dict | None) -> bool:
    return alert_topic_override(entry) == BELLWETHER_TOPIC


def sends_dispatch_copy(topic: str) -> bool:
    """False for a topic whose pushes are never duplicated onto `dispatch`."""
    return topic not in NO_DISPATCH_TOPICS
