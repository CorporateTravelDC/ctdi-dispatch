"""ingest/census.py -- per-feed SWIM message-type census (2026-11-19, operator: "a sweep of what EVERY
subscription's message types are ... parse them all to enrich all our current data").

Why: every feed parser used to drop message types it did not know with a `log.debug` line, so whole
message families went unseen for months (FDPS HF/RH until 2026-08-17, HU until 08-30; on 2026-11-19
the FDPS handler still threw away AH/HU and never parsed NP/NU/DH/HV/HR). This module makes every
type visible, cheaply:

  - record(feed, msg_type, handled, sample=None): an in-memory counter bump (no I/O per message).
  - flush(): every FLUSH_INTERVAL_S the counters go to swim_message_census in ONE batched write
    (hour bucket x feed x type x handled), and once per UTC hour an INFO line per feed summarises it.
  - an unhandled type is warned ONCE per (feed, type) per hour -- never a silent drop.
  - a capped raw-sample store (SAMPLES_PER_TYPE_PER_DAY per type per UTC day) under SAMPLE_DIR, so the
    next parsers are built on real structure. Samples are written 0600, aircraft identifiers redacted
    (callsign, registration, ICAO address, beacon code: the structure stays, the identity goes), and
    aged out after SAMPLE_KEEP_DAYS.
  - a "dropped type" alert: an unhandled type above DROP_ALERT_PER_HOUR in one hour logs a WARNING and
    raises one ops alert per (feed, type) per UTC day.

Fail-soft everywhere: a census failure is logged and never stops message processing.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

log = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 300.0
SAMPLE_DIR = os.environ.get("SWIM_SAMPLE_DIR", "/var/lib/corporatetraveldc/swim-samples")
SAMPLES_PER_TYPE_PER_DAY = 5
SAMPLE_KEEP_DAYS = 14
SAMPLE_MAX_BYTES = 64 * 1024
DROP_ALERT_PER_HOUR = int(os.environ.get("SWIM_CENSUS_DROP_ALERT_PER_HOUR", "100"))

_lock = threading.Lock()
_counts: dict[tuple[int, str, str, bool], int] = {}      # (hour, feed, type, handled) -> n
_hour_totals: dict[tuple[int, str, str, bool], int] = {}  # current hour, for summary/alerts
_warned: set[tuple[int, str, str]] = set()                # (hour, feed, type) already warned
_alerted: set[tuple[str, str, str]] = set()               # (day, feed, type) already alerted
_samples: dict[tuple[str, str, str], int] = {}            # (day, feed, type) -> samples written
_last_summary_hour: int | None = None
_samples_disabled = False

# attribute / element names whose VALUES identify an aircraft; redacted in stored samples
_ID_ATTRS = ("aircraftIdentification", "acid", "callsign", "callSign", "registration",
             "aircraftRegistration", "aircraftAddress", "icao24", "modeSCode", "mode_s",
             "beaconCode", "squawk", "tailNumber", "flightIdentification")
_ATTR_RE = re.compile(r'\b(' + "|".join(_ID_ATTRS) + r')="[^"]*"')
_ELEM_RE = re.compile(r'<((?:\w+:)?(' + "|".join(_ID_ATTRS) + r'))(\s[^>]*)?>[^<]*</\1>')
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _hour(t: float) -> int:
    return int(t // 3600) * 3600


def _day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def redact(text: str) -> str:
    """Replace aircraft-identifying values with REDACTED; keep tags and every other value."""
    text = _ATTR_RE.sub(lambda m: f'{m.group(1)}="REDACTED"', text)
    return _ELEM_RE.sub(lambda m: f"<{m.group(1)}{m.group(3) or ''}>REDACTED</{m.group(1)}>", text)


def record(feed: str, msg_type: str | None, handled: bool, sample: bytes | str | None = None,
           now: float | None = None) -> None:
    """Count one message (or one flight inside a batched document). Cheap: a dict bump."""
    try:
        t = time.time() if now is None else now
        h = _hour(t)
        mt = (msg_type or "?").strip()[:64] or "?"
        key = (h, feed, mt, bool(handled))
        warn = False
        with _lock:
            _counts[key] = _counts.get(key, 0) + 1
            _hour_totals[key] = _hour_totals.get(key, 0) + 1
            if not handled and (h, feed, mt) not in _warned:
                _warned.add((h, feed, mt))
                warn = True
        if warn:
            log.warning("census %s: unhandled message type %r (counted; sampled up to %d/day)",
                        feed, mt, SAMPLES_PER_TYPE_PER_DAY)
        if sample is not None and not handled:
            _maybe_sample(feed, mt, sample, t)
    except Exception as e:  # never let the census break ingest
        log.debug("census record failed: %s", e)


def sample(feed: str, msg_type: str, payload: bytes | str, now: float | None = None) -> None:
    """Store a capped sample of a HANDLED type too (e.g. AH/HU while their mapping is new)."""
    try:
        _maybe_sample(feed, (msg_type or "?")[:64], payload, time.time() if now is None else now)
    except Exception as e:
        log.debug("census sample failed: %s", e)


def _maybe_sample(feed: str, mt: str, payload: bytes | str, t: float) -> None:
    global _samples_disabled
    if _samples_disabled:
        return
    day = _day(t)
    k = (day, feed, mt)
    with _lock:
        n = _samples.get(k, 0)
        if n >= SAMPLES_PER_TYPE_PER_DAY:
            return
        _samples[k] = n + 1
    text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
    text = redact(text[:SAMPLE_MAX_BYTES])
    d = os.path.join(SAMPLE_DIR, _SAFE.sub("_", feed), day)
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        path = os.path.join(d, f"{_SAFE.sub('_', mt)}-{n + 1}.xml")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        _samples_disabled = True
        log.warning("census: sample store %s not writable (%s); samples disabled, counts continue", SAMPLE_DIR, e)


def _age_out(now: float) -> None:
    cutoff = _day(now - SAMPLE_KEEP_DAYS * 86400)
    try:
        for feed in os.listdir(SAMPLE_DIR):
            fd = os.path.join(SAMPLE_DIR, feed)
            if not os.path.isdir(fd):
                continue
            for day in os.listdir(fd):
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and day < cutoff:
                    dd = os.path.join(fd, day)
                    for name in os.listdir(dd):
                        os.unlink(os.path.join(dd, name))
                    os.rmdir(dd)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.debug("census age-out: %s", e)


def _alert(feed: str, mt: str, n: int) -> None:
    msg = (f"SWIM census: {feed} is dropping message type {mt!r} ({n} in the last hour). "
           f"Samples: {SAMPLE_DIR}/{feed}/. Parser needed.")
    log.warning(msg)
    try:
        from common import ntfy_push
        ntfy_push.send(topic="ops-health", title="SWIM: unparsed message type", message=msg[:200], priority=3)
    except Exception as e:
        log.debug("census alert push failed: %s", e)


def flush(now: float | None = None, db=None) -> int:
    """Write the pending counts (one batched write), the hourly summary and the drop alerts."""
    global _last_summary_hour
    t = time.time() if now is None else now
    with _lock:
        snap = dict(_counts)
        _counts.clear()
    if snap:
        try:
            if db is None:
                from common import db as _db
                db = _db
            db.add_swim_message_census([(h, f, m, int(hd), n) for (h, f, m, hd), n in snap.items()])
        except Exception as e:
            log.warning("census flush failed (%d rows kept for the next flush): %s", len(snap), e)
            with _lock:
                for k, v in snap.items():
                    _counts[k] = _counts.get(k, 0) + v
    h = _hour(t)
    if _last_summary_hour is None:
        _last_summary_hour = h
    if h > _last_summary_hour:
        _summarise(_last_summary_hour, t)
        _last_summary_hour = h
        _age_out(t)
    return len(snap)


def _summarise(hour: int, now: float) -> None:
    with _lock:
        rows = {k: v for k, v in _hour_totals.items() if k[0] == hour}
        for k in rows:
            del _hour_totals[k]
        for w in [w for w in _warned if w[0] <= hour]:
            _warned.discard(w)
    by_feed: dict[str, list[str]] = {}
    for (_, feed, mt, handled), n in sorted(rows.items(), key=lambda kv: -kv[1]):
        by_feed.setdefault(feed, []).append(f"{mt}{'' if handled else '(DROPPED)'}={n}")
        if not handled and n >= DROP_ALERT_PER_HOUR:
            k = (_day(now), feed, mt)
            if k not in _alerted:
                _alerted.add(k)
                _alert(feed, mt, n)
    hh = time.strftime("%H:00Z", time.gmtime(hour))
    for feed, parts in by_feed.items():
        log.info("census %s %s: %s", feed, hh, " ".join(parts[:40]))


_flush_stop = threading.Event()


def start_flush_thread() -> threading.Thread:
    def loop():
        while not _flush_stop.wait(FLUSH_INTERVAL_S):
            flush()
        flush()
    th = threading.Thread(target=loop, daemon=True, name="swim-census-flush")
    th.start()
    return th


def stop_flush_thread() -> None:
    _flush_stop.set()


_ROOT_RE = re.compile(rb"<(?![?!])(?:[\w.-]+:)?([\w.-]+)")
_MSGTYPE_RE = re.compile(rb'\bmsgType="([^"]{1,64})"')


def doc_type(payload: bytes) -> str:
    """Cheap document type: the root element's local name, plus msgType="..." when present (TFMS)."""
    head = payload[:4096]
    m = _ROOT_RE.search(head)
    root = m.group(1).decode("ascii", "replace") if m else "?"
    mt = _MSGTYPE_RE.search(head)
    return f"{root}/{mt.group(1).decode('ascii', 'replace')}" if mt else root


def _reset_for_tests() -> None:
    global _last_summary_hour, _samples_disabled
    with _lock:
        _counts.clear(); _hour_totals.clear(); _warned.clear(); _alerted.clear(); _samples.clear()
    _last_summary_hour = None
    _samples_disabled = False
