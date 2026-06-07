"""
src/shared/watchlist.py

Shared watchlist management. Used by both the ingest container
(FDPS/STDDS event matching) and the poller (Amtrak, REST-polled flight data).

ntfy topic routing (canonical):
  Flights:  fire "flight-alerts" + "dispatch" simultaneously
  Trains:   fire "train-alerts"  + "dispatch" simultaneously
  Both:     domain topic = full detail; dispatch = concise bottom line
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

import requests

from common import db

log = logging.getLogger("shared.watchlist")

PERMANENT_WATCHLIST_DIR = Path("/opt/corporatetraveldc/watchlists")
NTFY_BASE = os.environ.get("NTFY_URL", "http://host.containers.internal:2586")
NTFY_USER = os.environ.get("NTFY_USER", "")
NTFY_PASS = os.environ.get("NTFY_PASS", "")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")

EntryType = Literal["flight", "train"]

_ntfy_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ntfy")

# Dedup window: don't re-fire the same event_type for the same entry within this many seconds.
_DEDUP_WINDOW_SECS = 300  # 5 minutes
_dedup_lock = threading.Lock()
_dedup_cache: dict[str, float] = {}  # key = f"{entry_id}:{event_type}" → last fired epoch


def _dedup_key(entry_id: str, event_type: str) -> str:
    return f"{entry_id}:{event_type}"


def _check_dedup(entry_id: str, event_type: str) -> bool:
    """Return True if we should suppress (already fired within dedup window)."""
    key = _dedup_key(entry_id, event_type)
    now = time.time()
    with _dedup_lock:
        last = _dedup_cache.get(key, 0.0)
        if now - last < _DEDUP_WINDOW_SECS:
            return True
        _dedup_cache[key] = now
        return False


def get_active_entries(entry_type: EntryType | None = None) -> list[dict]:
    """Return all non-expired watchlist entries from DB."""
    return db.get_watchlist_entries(entry_type=entry_type)


def watchlist_event_hit(entry_id: str, event_summary: str,
                        event_detail: dict,
                        priority: int = 3) -> None:
    """
    Called when a watched entity has a status event.
    Fires dual ntfy push (domain topic + dispatch) and writes to watchlist_history.
    Deduplicates: same entry_id + event_type will not fire again within 5 minutes.
    """
    entries = db.get_watchlist_entries()
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if entry is None:
        log.warning("watchlist_event_hit: entry %s not found", entry_id)
        return

    event_type = event_detail.get("watchlist_trigger", "status_change")
    if _check_dedup(entry_id, event_type):
        log.debug("watchlist dedup suppressed: %s / %s", entry_id, event_type)
        return

    fired_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ident = entry["identifier"]
    etype = entry["entry_type"]

    if etype == "flight":
        domain_topic = "flight-alerts"
        origin = entry.get("origin") or ""
        dest = entry.get("destination") or ""
        route = f"{origin}→{dest}" if origin or dest else ""
        detail_body = f"{ident} {route}\n{event_summary}"
        dispatch_body = f"Flight {ident}: {event_summary}"
    else:
        domain_topic = "train-alerts"
        route_name = entry.get("route_name") or ""
        detail_body = f"{route_name} #{ident}\n{event_summary}"
        dispatch_body = f"Train {ident}: {event_summary}"

    title = ("FLT " if etype == "flight" else "TRN ") + ident + ": " + event_summary[:60]

    _fire_ntfy_dual(domain_topic, title, detail_body, dispatch_body, priority)

    db.insert_watchlist_history(
        entry_id=entry_id,
        entry_type=etype,
        identifier=ident,
        event_type=event_type,
        event_summary=event_summary,
        event_detail=event_detail,
        fired_at=fired_at,
    )
    db.update_watchlist_last_event(entry_id, event_summary, fired_at)


def sweep_expired_transient(db_conn=None) -> int:
    """
    Remove transient entries where auto_remove_at < now.
    Writes "auto_expired" record to watchlist_history for each.
    Returns count removed. Called by poller every 60s.
    db_conn param accepted but unused (uses common.db connection pool).
    """
    expired = db.sweep_expired_watchlist_entries()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for entry in expired:
        db.insert_watchlist_history(
            entry_id=entry["id"],
            entry_type=entry["entry_type"],
            identifier=entry["identifier"],
            event_type="auto_expired",
            event_summary=f"Auto-expired at {entry.get('auto_remove_at', now_iso)}",
            event_detail={"auto_remove_at": entry.get("auto_remove_at")},
            fired_at=now_iso,
        )
        log.info("watchlist: auto-expired %s %s", entry["entry_type"], entry["identifier"])
    return len(expired)


def _fire_ntfy_dual(domain_topic: str, title: str, detail_body: str,
                    dispatch_body: str, priority: int) -> None:
    """
    Fire two ntfy pushes in parallel (non-blocking via thread pool):
      1. domain_topic with full detail_body
      2. "dispatch" with concise dispatch_body
    Both use the same title and priority.
    """
    def _push(topic: str, body: str) -> None:
        url = f"{NTFY_BASE}/{topic}"
        # HTTP headers must be ASCII — strip/replace non-ASCII chars in title
        safe_title = title.encode("ascii", "replace").decode("ascii")
        headers = {
            "Content-Type": "text/plain",
            "X-Priority": str(priority),
            "X-Title": safe_title,
        }
        auth = None
        if NTFY_TOKEN:
            # Strip label suffix (token stored as "token:label" in secrets.env)
            headers["Authorization"] = f"Bearer {NTFY_TOKEN.split(':')[0]}"
        elif NTFY_USER:
            auth = (NTFY_USER, NTFY_PASS)
        try:
            resp = requests.post(url, data=body.encode("utf-8"),
                                 headers=headers, auth=auth, timeout=10)
            resp.raise_for_status()
            log.debug("ntfy push OK: topic=%s priority=%d", topic, priority)
        except Exception as e:
            log.error("ntfy push FAILED: topic=%s error=%s", topic, e)

    f1 = _ntfy_pool.submit(_push, domain_topic, detail_body)
    f2 = _ntfy_pool.submit(_push, "dispatch", dispatch_body)
    futures_wait([f1, f2], timeout=15)


# ── Permanent watchlist file watcher ─────────────────────────────────────────

class WatchlistFileWatcher:
    """
    Reads permanent watchlist JSON files at startup and re-reads on mtime change.
    Upserts entries into watchlist_entries with tier="permanent".
    Detects removals and writes "permanent_removed" history records.
    Run by the poller — NOT by the ingest container.
    """
    POLL_INTERVAL = 60  # seconds between mtime checks

    _FILE_MAP: dict[str, str] = {
        "permanent_flights.json": "flight",
        "permanent_trains.json": "train",
    }

    def __init__(self) -> None:
        self._mtimes: dict[str, float] = {}
        self._loaded_ids: dict[str, set[str]] = {}  # filename → set of entry IDs

    def start(self, stop_event: threading.Event) -> None:
        """Load files immediately, then poll in background thread."""
        self._load_all()
        t = threading.Thread(target=self._poll_loop, args=(stop_event,),
                             daemon=True, name="watchlist-file-watcher")
        t.start()

    def _poll_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            stop.wait(self.POLL_INTERVAL)
            if stop.is_set():
                break
            self._check_for_changes()

    def _check_for_changes(self) -> None:
        for filename in self._FILE_MAP:
            path = PERMANENT_WATCHLIST_DIR / filename
            try:
                mtime = path.stat().st_mtime if path.exists() else 0.0
            except OSError:
                mtime = 0.0
            if mtime != self._mtimes.get(filename, -1):
                self._load_file(filename, path)

    def _load_all(self) -> None:
        for filename in self._FILE_MAP:
            path = PERMANENT_WATCHLIST_DIR / filename
            self._load_file(filename, path)

    def _load_file(self, filename: str, path: Path) -> None:
        entry_type = self._FILE_MAP[filename]
        if not path.exists():
            log.warning("watchlist: %s not found, skipping", path)
            self._mtimes[filename] = 0.0
            return

        try:
            data = json.loads(path.read_text())
            entries = data.get("watchlist", [])
        except (json.JSONDecodeError, OSError) as e:
            log.error("watchlist: failed to parse %s: %s — keeping existing DB entries", path, e)
            return

        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        new_ids: set[str] = set()

        for raw in entries:
            entry_id = raw.get("id")
            ident = raw.get("identifier")
            if not entry_id or not ident:
                log.warning("watchlist: skipping entry missing id/identifier in %s", filename)
                continue

            new_ids.add(entry_id)
            db.upsert_watchlist_entry({
                "id": entry_id,
                "entry_type": entry_type,
                "tier": "permanent",
                "identifier": ident,
                "origin": raw.get("origin"),
                "destination": raw.get("destination"),
                "route_name": raw.get("route_name"),
                "scheduled_departure": None,
                "scheduled_arrival": None,
                "auto_remove_at": None,
                "added_at": raw.get("added", now_iso),
                "added_by": raw.get("added_by", "operator"),
                "notes": raw.get("notes"),
                "last_event_at": None,
                "last_event_summary": None,
            })

        # Remove entries that were in the last load but are gone from the file.
        old_ids = self._loaded_ids.get(filename, set())
        removed = old_ids - new_ids
        for removed_id in removed:
            entry = db.delete_watchlist_entry(removed_id)
            if entry:
                db.insert_watchlist_history(
                    entry_id=removed_id,
                    entry_type=entry_type,
                    identifier=entry.get("identifier", removed_id),
                    event_type="permanent_removed",
                    event_summary="Removed from permanent watchlist file",
                    event_detail={"filename": filename},
                    fired_at=now_iso,
                )
                log.info("watchlist: permanent entry %s removed (not in %s)", removed_id, filename)

        self._loaded_ids[filename] = new_ids
        self._mtimes[filename] = mtime
        if new_ids:
            log.info("watchlist: loaded %d permanent %s entries from %s",
                     len(new_ids), entry_type, filename)
