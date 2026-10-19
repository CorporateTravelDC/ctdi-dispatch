"""VIP-listed aircraft (2026-10-18, operator: ACARS flag pushes at p5 for VIP-listed
aircraft, p4 otherwise).

"VIP-listed" = an entry in watchlists/permanent_flights.json whose route_name is one
of VIP_ROUTE_NAMES (compared without hyphens/spaces, each "/"-separated part on its
own, so "E4B/E4C" counts as E-4B), or whose notes mention AF1, AF2, SAM (SAM, SAM41
...) or Nightwatch.

Source of the record, in order:
  1. the file itself, read the way shared.watchlist.WatchlistFileWatcher reads it
     (PERMANENT_WATCHLIST_DIR / "permanent_flights.json", data["watchlist"]), matched
     by entry id; re-read when its mtime changes;
  2. when the file is not mounted in this container (ingest-core has no watchlists
     volume; only poller and web do), the watchlist_entries row itself if its tier is
     "permanent" -- the poller's file watcher copies route_name and notes from the
     file into that row verbatim.
Nothing here logs or returns an identifier.
"""
from __future__ import annotations

import json
import re
import threading

from shared.watchlist import PERMANENT_WATCHLIST_DIR

PERMANENT_FLIGHTS_FILE = "permanent_flights.json"
VIP_ROUTE_NAMES = frozenset({"VC25A", "VC25B", "C32", "C37", "C40", "C40C", "E4B"})
_VIP_NOTES_RE = re.compile(r"\b(?:AF[12]|SAM\d*|NIGHTWATCH)\b", re.IGNORECASE)

_lock = threading.Lock()
_cache: dict = {"mtime": None, "by_id": {}}


def is_vip_record(rec: dict | None) -> bool:
    """The predicate on one permanent_flights.json record (or its DB copy)."""
    if not rec:
        return False
    route = str(rec.get("route_name") or "").upper()
    parts = {re.sub(r"[\s-]", "", p) for p in route.split("/")}
    if parts & VIP_ROUTE_NAMES:
        return True
    return bool(_VIP_NOTES_RE.search(str(rec.get("notes") or "")))


def _file_records() -> dict:
    path = PERMANENT_WATCHLIST_DIR / PERMANENT_FLIGHTS_FILE
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    with _lock:
        if _cache["mtime"] != mtime:
            try:
                entries = json.loads(path.read_text()).get("watchlist", [])
                by_id = {e["id"]: e for e in entries if isinstance(e, dict) and e.get("id")}
            except (OSError, ValueError, AttributeError):
                by_id = {}
            _cache.update(mtime=mtime, by_id=by_id)
        return _cache["by_id"]


def is_vip_listed(entry: dict | None) -> bool:
    """True if this watchlist entry is a VIP-listed aircraft (module docstring)."""
    if not entry:
        return False
    recs = _file_records()
    if recs:
        return is_vip_record(recs.get(entry.get("id")))
    return entry.get("tier") == "permanent" and is_vip_record(entry)
