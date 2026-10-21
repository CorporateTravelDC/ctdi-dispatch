"""demo.ladd_timing -- timing anti-correlation for LADD-listed aircraft on the
demo path (operator decisions 2026-10-20). Applied ONLY by
scripts/scrub-demo-source.py, after the Q substitution, to the demo copy;
production and every tailnet (live) path are never touched.

Threat model. A Q mark hides the registration, not the timing. Someone
physically at an FBO (an employee, an onlooker, a stalker) sees who arrives
when; watching the demo, they can line up "a jet arrived at 14:07" with "the
Q-mark that landed at 14:07" and learn which real aircraft each Q-mark is.
Timing is the identifier the Q-mark does not hide. Two defences:

1. Per-leg bucketing with a hidden rotating offset. Every time on a LADD leg
   becomes floor((t + offset) / BUCKET_S) * BUCKET_S. The offset is constant
   within a leg (so a leg stays internally consistent) and drawn from an HMAC
   under the production-only Q-registry key with its own domain label, so it
   is stable across scrub re-runs but cannot be computed without the key and
   is never stored. Offsets ROTATE per leg: each leg draws its own offset
   anywhere in [0, BUCKET), and consecutive legs of the same aircraft --
   two legs on one operational day, and the last leg of a day against the
   first of the next -- always differ by at least ROTATION_MARGIN_S, so there
   is no "always N minutes behind" to learn on any aircraft.
2. Dark point. A LADD leg's record ends at the dark point: the approach's
   inner marker where the chart data has one, else the runway threshold,
   else the confirmed wheels-on time; if none can be determined, everything
   near the airport and every arrival-side field is dropped (fail closed).
   Nothing at or after it -- landing time, ON/IN, gate, terminal, baggage,
   FBO, taxi, surface movement, ground positions -- enters the demo.

Leg identity: (Q mark, operational day, leg index). The operational day uses
the platform's 05:00 America/New_York rollover (profile day_rollover). The leg
index comes from SWIM, not from the text: the aircraft's legs that operational
day are read from flight_events (FDPS: departure and arrival times, by the real
registration or callsign -- in-process only, never written) and ordered by
departure. Each leg owns a time window -- from the midpoint between the
previous leg's arrival and its departure, to the same midpoint before the next
leg (without an arrival time: 90 minutes, or half the gap, before the next
departure). A reference is attributed to the leg whose window contains the time
it mentions: a record's departure time, else its earliest time; a sentence's
first time; else the row's own capture time. The destination is deliberately
not used, because brief prose often names a flight without one -- the window
rule gives the prose and the structured record of one leg the same index, so a
leg never shows two offsets (two would let a reader intersect two 15-minute
windows).

Offset chain. Leg k's offset is the first HMAC draw (domain label, Q mark,
day, k, attempt) at least ROTATION_MARGIN_S from the previous leg's offset.
The chain starts after the aircraft's most recent operational day with no SWIM
leg, at most CHAIN_LOOKBACK_DAYS back, so every scrub of the same day computes
the same chain (idempotent; nothing is stored). When the cap is hit, the first
leg of the day is checked against the previous day's last leg computed from
its own chain; on a margin miss the whole aircraft-day is dropped.

Fail closed. If the leg order cannot be determined -- no SWIM leg for the
aircraft that operational day, the lookup fails, or the record carries only a
redacted hex (no identifier to look up) -- every LADD record and sentence for
that aircraft-day is DROPPED from the demo, not shown with a coarser rule.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

BUCKET_S = 15 * 60
ROTATION_MARGIN_S = 4 * 60
CHAIN_LOOKBACK_DAYS = 7      # offset chain starts after the last empty op day, at most this far back
PRE_DEPARTURE_S = 90 * 60    # leg window opens this long before departure when the previous arrival is unknown
DRAW_ATTEMPTS = 64           # HMAC draws per leg before giving up (fail closed); ~1e-21 odds of needing more
DOMAIN = b"demo-ladd-leg-offset"
OPDAY_TZ = ZoneInfo("America/New_York")
OPDAY_ROLLOVER = timedelta(hours=5)
THRESHOLD_NM = 1.0          # first track point this close to a destination threshold = threshold crossing
MARKER_NM = 0.3             # first track point this close to an inner marker = IM crossing
FALLBACK_RADIUS_NM = 10.0   # fail closed: no track point this close to the destination (or any airport)

ET = ZoneInfo("America/New_York")
Q_MARK_RE = re.compile(r"\bQ[0-9A-HJ-NPR-Z]{5}\b")
HEX_REDACTED = "[hex-redacted]"

# Arrival-side keys and phrases: removed from any LADD record, whatever the time.
ARRIVAL_KEY_RE = re.compile(
    r"(^|_)(on|in|ata|landed|landing|arriv\w*|actual_on|actual_in|on_time|in_time|touchdown|wheels_on|"
    r"gate\w*|terminal\w*|baggage\w*|belt\w*|carousel\w*|fbo\w*|taxi\w*|surface\w*|parking\w*|parked|"
    r"stand\w*|ramp\w*|block_in\w*|in_block\w*|on_block\w*)($|_)", re.I)
ARRIVAL_VALUE_RE = re.compile(
    r"^\s*(on|in|landed|arrived|arriving|at gate|parked|taxi\w*|on[- ]?block|in[- ]?block|deplan\w*)\s*$", re.I)
ARRIVAL_TEXT_RE = re.compile(
    r"\b(land(?:ed|ing|s)?|arriv\w*|touch(?:ed)?\s?down|wheels[- ]on|on[- ]?block|in[- ]?block|gate|terminal|"
    r"baggage|belt|carousel|FBO|taxi\w*|parked|ramp|deplan\w*)\b", re.I)
OOOI_ARRIVAL_RE = re.compile(r"\b(ON|IN)\b")          # upper-case OOOI phase tokens only
DEST_KEYS = ("destination", "dest", "arrival_airport", "arr", "to", "destination_icao", "dest_icao")
WHEELS_ON_KEYS = ("actual_on", "on_time", "wheels_on", "landed_at", "ata")
TIME_KEY_RE = re.compile(r"(time|_at$|^at$|eta|etd|sta|std|epoch|^ts$|timestamp|^t$|seen)", re.I)

ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?\b")
CLOCK_RE = re.compile(r"\b(\d{2}):?(\d{2})\s?(Z|UTC|ET|EDT|EST)\b")


def opday(dt: datetime) -> date:
    """Operational day: America/New_York, rolling over at 05:00."""
    return (dt.astimezone(OPDAY_TZ) - OPDAY_ROLLOVER).date()


class LegTimer:
    """Offsets and bucketing for one scrub run. Holds the key; never logs it."""

    def __init__(self, key: bytes):
        if len(key) < 32:
            raise ValueError("leg-offset key shorter than 32 bytes")
        self._key = key

    def _draw(self, mark: str, day: date, k: int, attempt: int) -> int:
        msg = b"|".join((DOMAIN, mark.upper().encode(), day.isoformat().encode(),
                         str(k).encode(), str(attempt).encode()))
        return int.from_bytes(hmac.new(self._key, msg, hashlib.sha256).digest()[:8], "big") % BUCKET_S

    def _next(self, mark: str, day: date, k: int, prev: int | None) -> int | None:
        for attempt in range(DRAW_ATTEMPTS):
            x = self._draw(mark, day, k, attempt)
            if prev is None or abs(x - prev) >= ROTATION_MARGIN_S:
                return x
        return None

    def _chain(self, mark: str, day: date, legs_on: Callable[[date], int | None]) -> tuple[list[int] | None, bool]:
        """(offsets of every leg on `day`, capped). None when any day in the
        chain has an unknown leg count or a draw fails."""
        start, capped = day, True
        for j in range(1, CHAIN_LOOKBACK_DAYS + 1):
            n = legs_on(day - timedelta(days=j))
            if n is None:
                return None, True
            if n == 0:
                start, capped = day - timedelta(days=j - 1), False
                break
            start = day - timedelta(days=j)
        prev: int | None = None
        d = start
        while True:
            n = legs_on(d)
            if n is None:
                return None, capped
            offs = []
            for k in range(n):
                prev = self._next(mark, d, k, prev)
                if prev is None:
                    return None, capped
                offs.append(prev)
            if d == day:
                return offs, capped
            d += timedelta(days=1)

    def offset(self, mark: str, day: date, k: int, legs_on: Callable[[date], int | None]) -> int | None:
        """Seconds in [0, BUCKET_S) for leg k of `mark` on operational day
        `day`; legs_on(d) is the aircraft's SWIM leg count on d (None =
        unknown). None = cannot be determined: the caller drops (fail closed)."""
        offs, capped = self._chain(mark, day, legs_on)
        if offs is None or not 0 <= k < len(offs):
            return None
        if capped:
            prev_offs, _ = self._chain(mark, day - timedelta(days=1), legs_on)
            if not prev_offs or abs(offs[0] - prev_offs[-1]) < ROTATION_MARGIN_S:
                return None
        return offs[k]

    @staticmethod
    def bucket(epoch: float, offset: int) -> int:
        return int(math.floor((epoch + offset) / BUCKET_S) * BUCKET_S)


def leg_index(legs: list[tuple[float, float | None]], t: float) -> int:
    """Index of the leg whose window contains t. legs: [(departure, arrival or
    None)] sorted by departure. Window boundary before leg k+1: the midpoint of
    leg k's arrival and leg k+1's departure, else PRE_DEPARTURE_S (or half the
    gap) before that departure."""
    k = 0
    for j in range(1, len(legs)):
        dep_prev, arr_prev = legs[j - 1]
        dep = legs[j][0]
        if arr_prev is not None and dep_prev <= arr_prev <= dep:
            edge = (arr_prev + dep) / 2
        else:
            edge = dep - min(PRE_DEPARTURE_S, (dep - dep_prev) / 2)
        if t >= edge:
            k = j
    return k


def opday_bounds(day: date) -> tuple[float, float]:
    """[start, end) epochs of an operational day."""
    start = datetime(day.year, day.month, day.day, tzinfo=OPDAY_TZ) + OPDAY_ROLLOVER
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


CALLSIGN_RE = re.compile(r"([A-Z]{3})(\d{1,4}[A-Z]{0,2})")   # ICAO airline callsign -> (airline, flight_num)


class SwimLegs:
    """The aircraft's SWIM legs per operational day, from flight_events (FDPS),
    by the REAL identifier (registration or callsign, both registry forms).
    Read in-process through the scrub's read-only source; identifiers are never
    logged or stored. Any lookup failure returns None (fail closed)."""

    def __init__(self, source_factory: Callable[[], Any], keys: Callable[[str], list] | None = None):
        self._src = source_factory
        self._keys = keys or (lambda s: [s.strip().upper()])
        self._cache: dict = {}

    def legs(self, idents: frozenset, day: date) -> list[tuple[float, float | None]] | None:
        if not idents:
            return None
        ck = (idents, day)
        if ck in self._cache:
            return self._cache[ck]
        keys = sorted({k for i in idents for k in self._keys(i)})
        pairs = sorted({(m.group(1), m.group(2)) for i in idents for m in [CALLSIGN_RE.fullmatch(i)] if m})
        lo, hi = opday_bounds(day)
        # Two index-backed halves (a single OR seq-scans ~1M rows): registration
        # (idx_flight_events_registration_upper) and callsign as airline +
        # number (idx_flight_events_airline_num).
        sql = ("SELECT departure_time, arrival_time FROM flight_events WHERE registration IS NOT NULL AND "
               "UPPER(registration) IN (" + ",".join("?" * len(keys)) + ") AND departure_time >= ? AND departure_time < ?")
        args: list = [*keys, lo, hi]
        if pairs:
            sql += (" UNION ALL SELECT departure_time, arrival_time FROM flight_events WHERE ("
                    + " OR ".join("(airline = ? AND flight_num = ?)" for _ in pairs)
                    + ") AND departure_time >= ? AND departure_time < ?")
            args += [x for pr in pairs for x in pr] + [lo, hi]
        try:
            with self._src() as s:
                rows = s.execute(sql, tuple(args)).fetchall()
        except Exception:  # noqa: BLE001 -- unknown = drop, never a guess
            self._cache[ck] = None
            return None
        seen: dict[int, tuple[float, float | None]] = {}
        for r in rows:
            dep = r[0] if not isinstance(r, dict) else r["departure_time"]
            arr = r[1] if not isinstance(r, dict) else r["arrival_time"]
            if dep is None:
                continue
            slot = int(float(dep) // 600)               # one leg per 10-min departure slot (FDPS re-files)
            if slot not in seen or (seen[slot][1] is None and arr is not None):
                seen[slot] = (float(dep), float(arr) if arr is not None else None)
        out = sorted(seen.values())
        self._cache[ck] = out
        return out


# ── dark point ──────────────────────────────────────────────────────────────

def _nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))


class Charts:
    """Chart geometry for the dark point, from the CIFP reference tables.
    markers(apt) -> [(lat, lon)] inner markers; thresholds(apt) -> [(lat, lon)];
    airports() -> {ident: (lat, lon)}. Inner markers are not in the CIFP
    tables loaded on this box (2026-10-20: fix types are ENROUTE_WPT,
    TERMINAL_WPT, AIRPORT, RUNWAY_THRESHOLD, VHF_NAVAID, NDB), so markers()
    returns [] until a parse adds a marker type -- the threshold step then
    applies."""

    MARKER_TYPES = ("INNER_MARKER", "MARKER_IM", "IM")

    def __init__(self, rows: Iterable[dict] = ()):
        self._m: dict[str, list] = {}
        self._t: dict[str, list] = {}
        self._a: dict[str, tuple] = {}
        for r in rows:
            apt = (r.get("parent_airport") or "").upper()
            pt = (float(r["lat"]), float(r["lon"]))
            t = (r.get("type") or "").upper()
            if t in self.MARKER_TYPES and apt:
                self._m.setdefault(apt, []).append(pt)
            elif t == "RUNWAY_THRESHOLD" and apt:
                self._t.setdefault(apt, []).append(pt)
            elif t == "AIRPORT":
                self._a[(r.get("ident") or "").upper()] = pt

    @classmethod
    def load(cls) -> "Charts":
        from common import db
        types = ("RUNWAY_THRESHOLD", "AIRPORT") + cls.MARKER_TYPES
        with db.conn() as c:
            rows = c.execute("SELECT ident, type, lat, lon, parent_airport FROM cifp_fixes WHERE type IN ("
                             + ",".join("?" * len(types)) + ")", types).fetchall()
        return cls(dict(r) for r in rows)

    def markers(self, apt: str) -> list:
        return self._m.get(apt.upper(), [])

    def thresholds(self, apt: str) -> list:
        return self._t.get(apt.upper(), [])

    def airports(self) -> dict:
        return self._a

    def airport(self, apt: str):
        a = apt.upper()
        return self._a.get(a) or self._a.get("K" + a) if a else None


def _point(p: Any) -> tuple[float, float, float] | None:
    """(lat, lon, epoch) of a track point dict, or None."""
    if not isinstance(p, dict):
        return None
    lat = p.get("lat", p.get("latitude"))
    lon = p.get("lon", p.get("lng", p.get("longitude")))
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    for k, v in p.items():
        if TIME_KEY_RE.search(str(k)):
            e = _epoch(v)
            if e is not None:
                return float(lat), float(lon), e
    return float(lat), float(lon), float("nan")


def dark_time(points: list, dest: str, wheels_on: float | None, charts: Charts) -> tuple[float | None, str]:
    """(epoch at which the leg goes dark, path). Path in im / threshold /
    wheels_on / fail_closed. fail_closed returns None: the caller drops
    every point near the destination (or any airport)."""
    pts = sorted((x for x in (_point(p) for p in points) if x and not math.isnan(x[2])), key=lambda x: x[2])
    for name, fixes, radius in (("im", charts.markers(dest) if dest else [], MARKER_NM),
                                ("threshold", charts.thresholds(dest) if dest else [], THRESHOLD_NM)):
        if fixes:
            for lat, lon, t in pts:
                if any(_nm(lat, lon, f[0], f[1]) <= radius for f in fixes):
                    return t, name
    if wheels_on is not None:
        return wheels_on, "wheels_on"
    return None, "fail_closed"


# ── time parsing ────────────────────────────────────────────────────────────

def _epoch(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if 1e12 <= v < 1e14:
            return v / 1000.0
        return float(v) if 1e9 <= v < 1e10 else None
    if isinstance(v, str):
        m = ISO_RE.fullmatch(v.strip())
        if m:
            return _iso_epoch(m)
    return None


def _iso_epoch(m: re.Match) -> float:
    d, hh, mm, ss, tz = m.group(1), m.group(2), m.group(3), m.group(4) or "00", m.group(5)
    dt = datetime.fromisoformat(f"{d}T{hh}:{mm}:{ss}")
    if tz in (None, "Z"):
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        sign = 1 if tz[0] == "+" else -1
        t = tz[1:].replace(":", "")
        dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=int(t[:2]), minutes=int(t[2:]))))
    return dt.timestamp()


def _fmt_like(original: Any, epoch: int) -> Any:
    """Rewrite a bucketed epoch in the original value's shape (UTC)."""
    dt = datetime.fromtimestamp(epoch, timezone.utc)
    if isinstance(original, (int, float)) and not isinstance(original, bool):
        return epoch * 1000 if original >= 1e12 else epoch
    s = original.strip()
    m = ISO_RE.fullmatch(s)
    sep = "T" if "T" in s else " "
    out = dt.strftime(f"%Y-%m-%d{sep}%H:%M:%S")
    tz = m.group(5) if m else None
    if tz == "Z":
        out += "Z"
    elif tz:
        out += "+00:00"
    return out


# ── the scrub step ──────────────────────────────────────────────────────────

_DROP = object()
DEPARTURE_KEYS = ("etd", "std", "departure_time", "departure", "actual_off", "actual_out", "off", "out",
                  "off_time", "out_time", "dep_time", "sched_dep")


class Scrubber:
    """Apply both defences to one row. counts() are classes only.

    legs: a SwimLegs (or any object with .legs(idents, day)). idents, per
    apply(): {Q mark: {real identifier, ...}} for this row -- in-process only."""

    def __init__(self, timer: LegTimer, charts: Charts, legs: Any = None):
        self.timer = timer
        self.charts = charts
        self.legs = legs
        self.counts: Counter = Counter()
        self._idents: dict[str, frozenset] = {}

    def apply(self, text: str, marks: set[str], row_at: datetime, hex_listed: bool,
              idents: dict[str, Iterable[str]] | None = None) -> str:
        if not marks and not hex_listed:
            return text
        self._idents = {m.upper(): frozenset(i.upper() for i in v) for m, v in (idents or {}).items()}
        day = opday(row_at)
        stripped = text.lstrip()
        if stripped[:1] in ("{", "["):
            import json
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if parsed is not None:
                out = self._walk(parsed, marks, hex_listed, day, row_at)
                if out is _DROP:
                    out = {} if isinstance(parsed, dict) else []
                return json.dumps(out, separators=(",", ":"), ensure_ascii=False)
        return self._text(text, marks, day, row_at)

    # leg resolution ---------------------------------------------------------
    def _leg_offset(self, mark: str, t: float) -> int | None:
        """Offset of the leg `mark` flies at time t, or None (drop)."""
        if self.legs is None or mark == "HEX":
            return None
        idents = self._idents.get(mark.upper())
        if not idents:
            return None
        day = opday(datetime.fromtimestamp(t, timezone.utc))
        legs = self.legs.legs(idents, day)
        if not legs:
            return None

        def legs_on(d: date) -> int | None:
            got = self.legs.legs(idents, d)
            return None if got is None else len(got)
        return self.timer.offset(mark, day, leg_index(legs, t), legs_on)

    # JSON ------------------------------------------------------------------
    def _is_ladd_record(self, node: dict, marks: set[str], hex_listed: bool) -> str | None:
        for v in node.values():
            if isinstance(v, str):
                for q in Q_MARK_RE.findall(v):
                    if q in marks:
                        return q
                if hex_listed and HEX_REDACTED in v:
                    return "HEX"
        return None

    def _walk(self, node: Any, marks: set[str], hex_listed: bool, day: date, row_at: datetime) -> Any:
        if isinstance(node, list):
            return [x for x in (self._walk(y, marks, hex_listed, day, row_at) for y in node) if x is not _DROP]
        if not isinstance(node, dict):
            return node
        q = self._is_ladd_record(node, marks, hex_listed)
        if q is None:
            out = {}
            for k, v in node.items():
                w = self._walk(v, marks, hex_listed, day, row_at)
                if w is not _DROP:
                    out[k] = w
            return out
        return self._ladd_record(node, q, row_at)

    def _rep_time(self, rec: dict, row_at: datetime) -> float:
        for k in DEPARTURE_KEYS:
            if k in rec:
                e = _epoch(rec[k])
                if e is not None:
                    return e
        times = [e for e in (self._first_epoch(v) for k, v in rec.items()
                              if TIME_KEY_RE.search(str(k)) or isinstance(v, (list, dict, str))) if e is not None]
        return min(times) if times else row_at.timestamp()

    def _first_epoch(self, v: Any) -> float | None:
        if isinstance(v, (str, int, float)):
            return _epoch(v)
        if isinstance(v, list):
            es = [x[2] for x in (_point(p) for p in v) if x and not math.isnan(x[2])]
            return min(es) if es else None
        return None

    def _ladd_record(self, rec: dict, mark: str, row_at: datetime) -> Any:
        off = self._leg_offset(mark, self._rep_time(rec, row_at))
        if off is None:
            self.counts["ladd aircraft-day dropped (no SWIM leg order)"] += 1
            return _DROP
        dest = ""
        for k in DEST_KEYS:
            v = rec.get(k)
            if isinstance(v, str) and 3 <= len(v.strip()) <= 4:
                dest = v.strip().upper()
                break
        wheels_on = None
        for k in WHEELS_ON_KEYS:
            if k in rec:
                wheels_on = _epoch(rec[k])
                if wheels_on is not None:
                    break
        out: dict = {}
        for k, v in rec.items():
            if ARRIVAL_KEY_RE.search(str(k)) and str(k).lower() not in DEST_KEYS:
                self.counts["ladd arrival field dropped"] += 1
                continue
            if isinstance(v, str) and ARRIVAL_VALUE_RE.match(v) and str(k).lower() in ("status", "phase", "state", "oooi"):
                self.counts["ladd arrival status dropped"] += 1
                continue
            if isinstance(v, list) and any(_point(p) for p in v):
                v = self._cut_track(v, dest, wheels_on)
            out[k] = self._shift(v, off, k)
        self.counts["ladd leg timed"] += 1
        return out

    def _cut_track(self, pts: list, dest: str, wheels_on: float | None) -> list:
        t_dark, path = dark_time(pts, dest, wheels_on, self.charts)
        self.counts[f"ladd dark point {path}"] += 1
        keep = []
        for p in pts:
            x = _point(p)
            if x is None:
                keep.append(p)
                continue
            lat, lon, t = x
            if t_dark is not None:
                if math.isnan(t) or t >= t_dark:
                    continue
            else:
                near = [self.charts.airport(dest)] if dest and self.charts.airport(dest) else list(self.charts.airports().values())
                if not near or math.isnan(t) or any(_nm(lat, lon, a[0], a[1]) <= FALLBACK_RADIUS_NM for a in near):
                    continue
            keep.append(p)
        self.counts["ladd track points dropped"] += len(pts) - len(keep)
        return keep

    def _shift(self, v: Any, off: int, key: Any = "") -> Any:
        if isinstance(v, dict):
            return {k: self._shift(x, off, k) for k, x in v.items()}
        if isinstance(v, list):
            return [self._shift(x, off, key) for x in v]
        if isinstance(v, str):
            e = _epoch(v)
            if e is not None:
                return _fmt_like(v, LegTimer.bucket(e, off))
            return self._shift_clock(v, off, None)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and TIME_KEY_RE.search(str(key)):
            e = _epoch(v)
            if e is not None:
                return _fmt_like(v, LegTimer.bucket(e, off))
        return v

    # text ------------------------------------------------------------------
    def _text(self, text: str, marks: set[str], day: date, row_at: datetime) -> str:
        parts = re.split(r"(?<=[.!?\n])", text)
        out = []
        for s in parts:
            qs = [q for q in Q_MARK_RE.findall(s) if q in marks]
            if not qs:
                out.append(s)
                continue
            if ARRIVAL_TEXT_RE.search(s) or OOOI_ARRIVAL_RE.search(s):
                self.counts["ladd arrival sentence dropped"] += 1
                if s.endswith("\n"):
                    out.append("\n")
                continue
            off = self._leg_offset(qs[0], self._sentence_time(s, row_at))
            if off is None:
                self.counts["ladd aircraft-day dropped (no SWIM leg order)"] += 1
                if s.endswith("\n"):
                    out.append("\n")
                continue
            out.append(self._shift_clock(s, off, row_at))
            self.counts["ladd leg timed"] += 1
        return "".join(out)

    @staticmethod
    def _sentence_time(s: str, row_at: datetime) -> float:
        """The first time a sentence mentions (ISO, or HH:MM with a zone on the
        row's UTC date), else the row's capture time."""
        m = ISO_RE.search(s)
        c = CLOCK_RE.search(s)
        if m and (not c or m.start() <= c.start()):
            return _iso_epoch(m)
        if c:
            hh, mm, zone = int(c.group(1)), int(c.group(2)), c.group(3).upper()
            if hh <= 23 and mm <= 59:
                base = row_at.astimezone(timezone.utc).date()
                tz = ET if zone in ("ET", "EDT", "EST") else timezone.utc
                return datetime(base.year, base.month, base.day, hh, mm, tzinfo=tz).timestamp()
        return row_at.timestamp()

    def _shift_clock(self, s: str, off: int, row_at: datetime | None) -> str:
        def iso(m: re.Match) -> str:
            return _fmt_like(m.group(0), LegTimer.bucket(_iso_epoch(m), off))
        s = ISO_RE.sub(iso, s)
        if row_at is None:
            return s
        base = row_at.astimezone(timezone.utc).date()

        def clock(m: re.Match) -> str:
            hh, mm, zone = int(m.group(1)), int(m.group(2)), m.group(3).upper()
            if hh > 23 or mm > 59:
                return m.group(0)
            tz = ET if zone in ("ET", "EDT", "EST") else timezone.utc
            dt = datetime(base.year, base.month, base.day, hh, mm, tzinfo=tz)
            b = datetime.fromtimestamp(LegTimer.bucket(dt.timestamp(), off), tz)
            sep = ":" if ":" in m.group(0) else ""
            gap = " " if " " in m.group(0) else ""
            return f"{b:%H}{sep}{b:%M}{gap}{m.group(3)}"
        return CLOCK_RE.sub(clock, s)
