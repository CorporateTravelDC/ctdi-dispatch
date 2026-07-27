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
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

import requests

from common import db
from common.acars import get_latest_phase as _acars_phase
from common.push_dedup import PushDedup, content_hash

log = logging.getLogger("shared.watchlist")

PERMANENT_WATCHLIST_DIR = Path("/opt/corporatetraveldc/watchlists")
NTFY_BASE = os.environ.get("NTFY_URL", "http://host.containers.internal:2586")
NTFY_USER = os.environ.get("NTFY_USER", "")
NTFY_PASS = os.environ.get("NTFY_PASS", "")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")

EntryType = Literal["flight", "train"]

_ntfy_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ntfy")

# Dedup window: don't re-fire the same event_type for the same entry within this many seconds.
# Persisted (not in-memory) — survives container restarts and is shared between
# the ingest container and the poller, which both call watchlist_event_hit() for
# the same entities via different paths (push-primary vs. REST fallback). An
# in-memory-only cache would let both fire independently during handoff windows
# or after either process restarts.
_DEDUP_WINDOW_SECS = 300  # 5 minutes
_watchlist_dedup = PushDedup("watchlist-event", dedup_secs=_DEDUP_WINDOW_SECS)

# Idempotency guard added 2026-07-21 -- narrowly targets the 401/403
# false-negative pattern documented in _fire_ntfy_dual()'s docstring below:
# ntfy intermittently returns 401/403 on this path while its own
# messages_published counter climbs healthily through the same window,
# meaning the request almost certainly reached the server and was
# published despite the client-visible error. The old retry loop treated
# every exception (including these) as "did not deliver" and resent the
# identical message, which is how a single takeoff event ended up firing
# "OFF -- airborne" 14 times in 90 minutes. This guard is content-hash +
# short-TTL (90s -- comfortably covers the 0.5s/1s retry backoff plus
# network round-trip, short enough not to interfere with the unrelated
# 5-minute _watchlist_dedup window above) and is checked ONLY on 401/403,
# not on genuine failure signals (timeouts, connection errors, 5xx),
# which still retry-and-resend exactly as before since those really do
# mean the message didn't get through.
_NTFY_AMBIGUOUS_STATUS_TTL_SECS = 90
_ntfy_ambiguous_dedup = PushDedup("ntfy-ambiguous-status", dedup_secs=_NTFY_AMBIGUOUS_STATUS_TTL_SECS)


# 2026-07-22: content-aware dedup. Previously ck was content_hash(event_type)
# -- a CONSTANT per trigger type -- so this was a pure blind timer: any
# tfms_tmi (or any other trigger) message that arrived after the 5-minute
# window elapsed re-fired a push no matter what it said. Root-caused via
# UAL1573's IAD_ZID/IAD_OUT TMI churn: TFMS continuously retransmits the
# SAME flow-constraint assignment (identical fca_id) with its boundary-
# crossing ETA nudged forward a minute or two each time -- completely
# routine SWIM chatter, not a real reassignment -- and every SWIM parser
# (tfms, fdps, ACARS/local_airspace) funnels through this same function,
# so any handler that fires on routine/refresh traffic showed the same
# push every ~5-15 min forever pattern.
#
# Fix: hash the actual event payload instead of the constant trigger name,
# with ISO-8601 timestamp values coarse-bucketed (_TS_BUCKET_MINUTES) so
# continuous small ETA/boundary-time refinement still hashes identically
# and gets suppressed, while a genuine identity or value change (different
# fca_id, a real reassignment, a schedule change beyond the bucket width)
# changes the hash and pushes immediately even inside the window.
_TS_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?')
_TS_BUCKET_MINUTES = 10


def _bucket_timestamp(value: str, bucket_minutes: int = _TS_BUCKET_MINUTES) -> str:
    """Round an ISO-8601 timestamp string to a coarse bucket so continuous
    small refinements (e.g. TFMS re-sending the same constraint with its
    ETA ticked forward a minute or two) hash identically, while a genuine
    jump still changes the hash."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    epoch_min = int(dt.timestamp() // 60)
    bucket = epoch_min - (epoch_min % bucket_minutes)
    return str(bucket)


def _normalize_detail_for_hash(event_detail: dict) -> dict:
    """Drop the redundant trigger key and coarse-bucket any ISO-8601
    timestamp values so continuous ETA/boundary-time refinement doesn't
    read as a new event -- only genuine identity/value changes do."""
    out = {}
    for k, v in sorted((event_detail or {}).items()):
        if k == "watchlist_trigger":
            continue
        if isinstance(v, str) and _TS_RE.match(v):
            out[k] = _bucket_timestamp(v)
        else:
            out[k] = v
    return out


def _check_dedup(entry_id: str, event_type: str, event_detail: dict) -> bool:
    """Return True if we should suppress (already fired within dedup window).
    Content-aware: same entry_id + event_type + sub-identity + same
    normalized payload within the window is suppressed; a real content
    change or the window elapsing pushes again.

    2026-07-22 follow-up fix: a single flight can carry MULTIPLE concurrent
    tfms_tmi assignments at once (e.g. IAD_ZID + IAD_OUT + -ZOB all active
    simultaneously -- confirmed real via UAL357/UAL1573 watchlist_history,
    these are genuinely distinct flow-constraint areas, not duplicates).
    The dedup key was previously just entry_id:event_type -- ONE slot per
    trigger TYPE, shared across every distinct fca. Since the three fca's
    round-robin through that single slot, each one always looks "different
    from whatever was stored last" (because the last write was a *different*
    fca), so NONE of them ever actually got suppressed even after the
    2026-07-21 content-hash fix -- that fix was necessary but not
    sufficient. Now folds a stable per-constraint identity (fca_id, falling
    back to fca_name) into the key itself so each concurrent constraint
    gets its own independent dedup slot and history."""
    sub_id = event_detail.get("fca_id") or event_detail.get("fca_name") or ""
    key = f"{entry_id}:{event_type}:{sub_id}" if sub_id else f"{entry_id}:{event_type}"
    normalized = _normalize_detail_for_hash(event_detail)
    ck = content_hash(json.dumps(normalized, sort_keys=True, default=str))
    if _watchlist_dedup.should_push(key, ck):
        _watchlist_dedup.record(key, ck)
        return False
    return True


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
    if _check_dedup(entry_id, event_type, event_detail):
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


def sweep_landed_flights() -> int:
    """
    Remove transient flight entries that are confirmed landed, or confirmed
    dead-with-no-anomaly, ahead of the auto_remove_at timer.

    Operator directive 2026-07-21: "ACARS+ADSB cross check as hex is landed
    == sweep, OR both dead after 1 hr of scheduled arrival without any
    other anomalies." Landed is read from oooi_phase == 'in', which is
    already populated by the ACARS-primary/ADS-B-fallback pipeline in
    poller._check_flight_airplanes_live (ACARS checked first, ADS-B used
    when ACARS has nothing -- effectively the cross-check the directive
    asks for) and by the schedule-inference fallback for ADS-B-dark legs.
    "Dead" entries (oooi_phase never left pre_departure -- never seen on
    either source at all) are swept once genuinely stale (1hr past
    scheduled_arrival) UNLESS an anomaly (identity/registration mismatch,
    diversion) was ever logged against them -- an anomaly means a human
    should look at it, not that it should quietly vanish. Called every
    FLIGHT_SWEEP_INTERVAL tick, right after the status-check pass that
    freshens oooi_phase/last_event_summary for the same entries.
    """
    removed = 0
    now = datetime.now(timezone.utc)
    entries = db.get_watchlist_entries(entry_type="flight", tier="transient")
    for entry in entries:
        entry_id = entry["id"]
        phase = entry.get("oooi_phase")
        reason = None
        event_type = None

        if phase == "in":
            reason = "landed (oooi_phase=in, ACARS/ADS-B confirmed)"
            event_type = "auto_swept_landed"
        else:
            sched_arr = entry.get("scheduled_arrival")
            if sched_arr and phase in (None, "pre_departure"):
                try:
                    arr_dt = datetime.fromisoformat(sched_arr.replace("Z", "+00:00"))
                except ValueError:
                    arr_dt = None
                if arr_dt and now > arr_dt + timedelta(hours=1):
                    history = db.get_watchlist_history(entry_id=entry_id, limit=50)
                    has_anomaly = any(
                        h.get("event_type") in ("identity_mismatch",
                                                 "identity_mismatch_reg",
                                                 "diversion")
                        for h in history
                    )
                    if not has_anomaly:
                        # Operator directive 2026-07-23: don't sweep an
                        # entry as dead/canceled/diverted purely because
                        # the periodic per-tick check never happened to
                        # catch a positive OOOI hit -- do one more live
                        # ACARS aggregator query right here, at the moment
                        # of deletion, as the actual validation gate. This
                        # is what UAL2160 needed 2026-07-22 (swept "dead",
                        # never got ADS-B or ACARS contact, cause never
                        # confirmed) -- a fresh check at sweep time, not
                        # just trusting the absence of earlier signal.
                        acars_check = None
                        try:
                            acars_check = _acars_phase(
                                entry["identifier"],
                                registration=entry.get("registration"),
                            )
                        except Exception as e:
                            log.debug("sweep_landed_flights ACARS check %s: %s",
                                     entry["identifier"], e)

                        if acars_check:
                            acars_phase_found, acars_msg = acars_check
                            if acars_phase_found == "in":
                                reason = "landed (ACARS confirmed at sweep time)"
                                event_type = "auto_swept_landed"
                            else:
                                # ACARS shows real activity the periodic
                                # sweep missed -- do NOT delete. Persist
                                # the phase found and let normal tracking
                                # continue from here instead of losing the
                                # entry.
                                db.update_watchlist_oooi_phase(
                                    entry_id, acars_phase_found,
                                    now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                )
                                log.warning(
                                    "watchlist: %s NOT swept -- ACARS shows "
                                    "phase=%s at sweep time despite no prior "
                                    "oooi progression (source=%s)",
                                    entry["identifier"], acars_phase_found,
                                    acars_msg.get("_source", "ACARS"),
                                )
                        else:
                            reason = ("dead -- no ACARS/ADS-B contact "
                                      "(confirmed via live aggregator check "
                                      "at sweep time), 1hr+ past scheduled "
                                      "arrival, no anomalies")
                            event_type = "auto_swept_dead"

        if reason:
            db.delete_watchlist_entry(entry_id)
            db.insert_watchlist_history(
                entry_id=entry_id,
                entry_type="flight",
                identifier=entry["identifier"],
                event_type=event_type,
                event_summary=f"Auto-swept: {reason}",
                event_detail={"reason": reason, "oooi_phase": phase,
                              "scheduled_arrival": entry.get("scheduled_arrival")},
                fired_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            log.info("watchlist: auto-swept flight %s -- %s", entry["identifier"], reason)
            removed += 1
    return removed


def _amtraker_still_live(train_number: str) -> bool:
    """Minimal live Amtraker existence check for the sweep-time dead
    validation in sweep_landed_trains() -- deliberately does not fire any
    watchlist event itself (that remains _check_train_amtraker's job on
    the normal per-tick sweep path); this only answers "does Amtraker
    currently know about this train number at all," as the last check
    before permanently deleting the entry."""
    base_url = os.environ.get("AMTRAKER_API_URL", "https://api.amtraker.com/v3")
    try:
        resp = requests.get(f"{base_url}/trains/{train_number}", timeout=15)
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        trains = resp.json()
        return bool(trains)
    except Exception as e:
        log.debug("_amtraker_still_live %s: %s", train_number, e)
        return False  # can't confirm either way -- fall through to existing dead logic


def sweep_landed_trains() -> int:
    """
    Train counterpart to sweep_landed_flights() -- same operator directive,
    applied via Amtraker status instead of ACARS/ADS-B. "Arrived" is read
    from last_event_summary, which for train entries is written ONLY by
    poller._check_train_amtraker's state text (unlike flights, no other
    event type shares that field for trains, so text matching is safe
    here). "Dead" entries (amtraker never returned a match, so
    last_event_summary was never set) are swept once 1hr+ past scheduled
    arrival, unless an anomaly was logged. Called every TRAIN_SWEEP_INTERVAL
    tick, right after the status-check pass.
    """
    removed = 0
    now = datetime.now(timezone.utc)
    entries = db.get_watchlist_entries(entry_type="train", tier="transient")
    for entry in entries:
        entry_id = entry["id"]
        last_summary = (entry.get("last_event_summary") or "").lower()
        reason = None
        event_type = None

        if "arrived" in last_summary:
            reason = "arrived (amtraker confirmed)"
            event_type = "auto_swept_landed"
        else:
            sched_arr = entry.get("scheduled_arrival")
            if sched_arr and not last_summary:
                try:
                    arr_dt = datetime.fromisoformat(sched_arr.replace("Z", "+00:00"))
                except ValueError:
                    arr_dt = None
                if arr_dt and now > arr_dt + timedelta(hours=1):
                    history = db.get_watchlist_history(entry_id=entry_id, limit=50)
                    has_anomaly = any(
                        h.get("event_type") in ("identity_mismatch",
                                                 "identity_mismatch_reg",
                                                 "diversion")
                        for h in history
                    )
                    if not has_anomaly:
                        # Operator directive 2026-07-23 (train counterpart
                        # of the ACARS re-check above): one more live
                        # Amtraker query right at sweep time before
                        # deleting, rather than trusting that
                        # last_event_summary being empty means truly dead.
                        found_live = _amtraker_still_live(entry["identifier"])
                        if found_live:
                            log.warning(
                                "watchlist: train #%s NOT swept -- Amtraker "
                                "shows live data at sweep time despite no "
                                "prior last_event_summary",
                                entry["identifier"],
                            )
                        else:
                            reason = ("dead -- no amtraker contact "
                                      "(confirmed via live re-check at sweep "
                                      "time), 1hr+ past scheduled arrival, "
                                      "no anomalies")
                            event_type = "auto_swept_dead"

        if reason:
            db.delete_watchlist_entry(entry_id)
            db.insert_watchlist_history(
                entry_id=entry_id,
                entry_type="train",
                identifier=entry["identifier"],
                event_type=event_type,
                event_summary=f"Auto-swept: {reason}",
                event_detail={"reason": reason,
                              "scheduled_arrival": entry.get("scheduled_arrival")},
                fired_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            log.info("watchlist: auto-swept train #%s -- %s", entry["identifier"], reason)
            removed += 1
    return removed


_NTFY_RETRY_ATTEMPTS = 3
_NTFY_RETRY_BACKOFF_SECS = 0.5  # doubles each retry: 0.5s, 1s


def _fire_ntfy_dual(domain_topic: str, title: str, detail_body: str,
                    dispatch_body: str, priority: int) -> None:
    """
    Fire two ntfy pushes in parallel (non-blocking via thread pool):
      1. domain_topic with full detail_body
      2. "dispatch" with concise dispatch_body
    Both use the same title and priority.

    Retry added 2026-07-20: observed intermittent 403 Forbidden responses
    on this exact code path across a 90+ minute window (dozens of
    occurrences, no fixed interval, spread across many different topics
    and call sites) with NO corresponding denial/error visible in ntfy's
    own server logs for the same windows, and ntfy's messages_published
    counter climbing steadily and healthily throughout. A manual replay
    of an identical request (same token, same topic, moments after a
    logged failure) succeeded immediately. This points to a transient
    client/network-path hiccup (rootless podman port-forwarding under
    concurrent load is the leading suspect, given six SWIM feed threads
    plus the poller/pusher/web containers all potentially hitting ntfy at
    once) rather than a deterministic auth/config problem -- root cause
    not yet fully confirmed, but real alerts were silently dropping on
    every one of these occurrences with no prior retry. Mitigated here
    with a short bounded retry (3 attempts, exponential backoff starting
    at 0.5s) so a transient failure doesn't cost a real alert; still logs
    at ERROR (with the response body, previously missing) if all retries
    are exhausted, so a genuine persistent failure remains visible.
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

        idem_key = content_hash(f"{topic}|{safe_title}|{body}|{priority}")
        last_exc: Exception | None = None
        last_body: str | None = None
        backoff = _NTFY_RETRY_BACKOFF_SECS
        for attempt in range(1, _NTFY_RETRY_ATTEMPTS + 1):
            try:
                resp = requests.post(url, data=body.encode("utf-8"),
                                     headers=headers, auth=auth, timeout=10)
                resp.raise_for_status()
                if attempt > 1:
                    log.info("ntfy push OK on retry %d/%d: topic=%s priority=%d",
                             attempt, _NTFY_RETRY_ATTEMPTS, topic, priority)
                else:
                    log.debug("ntfy push OK: topic=%s priority=%d", topic, priority)
                return
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status in (401, 403):
                    # Documented false-negative pattern -- don't resend, mark
                    # as probable-delivery and stop instead of risking a
                    # confirmed duplicate for a message that likely already went out.
                    if _ntfy_ambiguous_dedup.should_push(idem_key, idem_key):
                        _ntfy_ambiguous_dedup.record(idem_key, idem_key)
                        log.warning(
                            "ntfy %s on topic=%s -- known false-negative pattern "
                            "(publish counter climbs healthily through these), "
                            "treating as delivered, NOT resending: %s",
                            status, topic, e,
                        )
                    else:
                        log.warning(
                            "ntfy %s on topic=%s -- already marked probable-delivery "
                            "within %ds window, suppressing resend",
                            status, topic, _NTFY_AMBIGUOUS_STATUS_TTL_SECS,
                        )
                    return
                last_exc = e
                last_body = getattr(getattr(e, "response", None), "text", None)
                if attempt < _NTFY_RETRY_ATTEMPTS:
                    log.warning(
                        "ntfy push attempt %d/%d failed (topic=%s): %s -- retrying in %.1fs",
                        attempt, _NTFY_RETRY_ATTEMPTS, topic, e, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= 2
            except Exception as e:
                last_exc = e
                last_body = getattr(getattr(e, "response", None), "text", None)
                if attempt < _NTFY_RETRY_ATTEMPTS:
                    log.warning(
                        "ntfy push attempt %d/%d failed (topic=%s): %s -- retrying in %.1fs",
                        attempt, _NTFY_RETRY_ATTEMPTS, topic, e, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= 2

        log.error("ntfy push FAILED after %d attempts: topic=%s error=%s body=%s",
                  _NTFY_RETRY_ATTEMPTS, topic, last_exc, last_body)

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
        "permanent_vessels.json": "vessel",  # yachts/cruise ships, identifier=MMSI
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
                # subsection/show_national/show_regional/days_active/
                # sister_flight -- added 2026-07-21 alongside DB schema v19.
                # These were already present in the JSON files (from the
                # train-roster and flight day-pattern rebuild earlier this
                # session) but never threaded through to the DB until now.
                "subsection": raw.get("subsection"),
                "show_national": raw.get("show_national"),
                "show_regional": raw.get("show_regional"),
                "days_active": raw.get("days_active"),
                "sister_flight": raw.get("sister_flight"),
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
