"""
common/flight_instance.py
-------------------------
Flight-INSTANCE resolver (2026-10-13; operator requests 2026-10-11/12).

A flight NUMBER is not a flight. Three confirmed ways the number alone
points at the wrong airplane:
  - shuttles: one number flies several times a day;
  - both directions: AA1893 runs DCA->FLL in the morning and FLL->DCA in
    the afternoon, so a morning watch matched by callsign alone was
    advanced by the afternoon leg's messages;
  - regional operators: UAL4520 flew as GJS4520, and RPA4520 flew the same
    night on a different route -- codeshare_map can hold both, and only
    the route says which one a watch means.

So the key here is the INSTANCE: operating carrier + flight number +
origin + destination + scheduled date (UTC date of the scheduled
departure). Marketing numbers translate to operating ones through
codeshare_map (route-filtered -- its rows carry origin/destination), and
the hex/registration ride along where the FAA flight plan has them.

What lives here (all of it read-only against the DB; nothing in this
module writes a watch, a phase, or a push):
  - FlightInstanceKey / FlightInstance + resolve()       -- candidates from
    flight_ooooi_times (TFMS: scheduled + airline OOOI times) and
    flight_events (FDPS: hex/registration, runway ETA, status);
  - same_instance()                                       -- strict equality;
  - match_watch_entry()                                   -- the shared watch
    matcher the TFMS/TBFM parsers use (identifier OR codeshare operating
    callsign OR hex, AND the message's airports agree with the watch);
  - pair_events()                                         -- time-fidelity
    pairing inside one instance only;
  - decide_tail_change()                                  -- the tail-change
    guard (pure);
  - assess_inbound()                                      -- "where's my
    inbound" (pure).
See docs/FLIGHT_INSTANCE_RESOLVER.md for the rules and the wiring points.

OOOI authority is untouched by design: nothing here asserts a phase. The
inbound tracker READS the airline/SWIM times it is given; any caller that
wants to write a phase still goes through
db.update_watchlist_oooi_phase_authoritative.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable, Optional

from common.airline_codes import IATA_TO_ICAO, split_callsign

log = logging.getLogger(__name__)


# ── Airports ─────────────────────────────────────────────────────────────────

def norm_airport(code: str | None) -> str | None:
    """FAA 3-letter / ICAO K-prefixed 4-letter spellings of the same US field
    compare equal (KDCA == DCA). Same rule as fdps_parser._norm_airport and
    tfms_parser._norm_airport_code. Anything else passes through upper-cased."""
    if not code:
        return None
    c = code.strip().upper()
    if len(c) == 4 and c.startswith("K") and c[1:].isalpha():
        return c[1:]
    return c or None


def airports_agree(a: str | None, b: str | None) -> Optional[bool]:
    """True / False / None (= cannot tell). None when either side is missing,
    or when the two are in different code systems we cannot bridge without a
    table (EGLL vs LHR). A 4-letter code ending in the 3-letter one (CYYC /
    YYC, PHNL / HNL) counts as agreement. Callers reject only on False --
    "unknown" must never turn into a mismatch."""
    na, nb = norm_airport(a), norm_airport(b)
    if not na or not nb:
        return None
    if na == nb:
        return True
    if len(na) == len(nb):
        return False
    short, long_ = (na, nb) if len(na) < len(nb) else (nb, na)
    if len(short) == 3 and len(long_) == 4 and long_.endswith(short):
        return True
    return None


# ── Times ────────────────────────────────────────────────────────────────────

def to_epoch(value) -> float | None:
    """Epoch seconds from an epoch number, a datetime, ISO-8601 ('Z' or
    offset), or the TFMS compact YYYYMMDDHHMM[SS] form. None if unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        pass
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    return None


def utc_date(value) -> str | None:
    """'YYYY-MM-DD' (UTC) of a scheduled departure, or a bare date string /
    date passed straight through. None if unknown."""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return value.strip()
    ep = to_epoch(value)
    if ep is None:
        return None
    return datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%Y-%m-%d")


_WATCH_ID_TZ = "America/New_York"


def watch_id_date(scheduled_departure=None, now_epoch: float | None = None) -> str:
    """'YYYYMMDD' date part of a deterministic watch id
    (wl-<type>-<ident>-<YYYYMMDD>).

    2026-10-22: the id used the UTC date of "now", so a watch added at
    ~19:00 ET and re-added after 20:00 ET (already the next UTC day) got a
    new id and a duplicate watch for the same leg. The date is now the
    America/New_York date of the leg's scheduled departure when one is
    given (a bare YYYY-MM-DD is taken as-is; a naive timestamp is read as
    UTC, same as to_epoch), else the America/New_York date of now. The
    format is unchanged, so existing ids keep working (not migrated)."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(_WATCH_ID_TZ)
    if scheduled_departure not in (None, ""):
        if isinstance(scheduled_departure, date) and not isinstance(scheduled_departure, datetime):
            return scheduled_departure.strftime("%Y%m%d")
        if isinstance(scheduled_departure, str) and re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", scheduled_departure.strip()):
            return scheduled_departure.strip().replace("-", "")
        ep = to_epoch(scheduled_departure)
        if ep is not None:
            return datetime.fromtimestamp(ep, tz=tz).strftime("%Y%m%d")
    ref = time.time() if now_epoch is None else float(now_epoch)
    return datetime.fromtimestamp(ref, tz=tz).strftime("%Y%m%d")


def _fmt_utc(ep: float | None) -> str:
    if ep is None:
        return "?"
    return datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%H:%MZ")


# ── Identifiers ──────────────────────────────────────────────────────────────

_ATC_ALNUM_RE = re.compile(r"^([A-Z]{3})(\d[A-Z0-9]{1,3})$")
_HEX6_RE = re.compile(r"^[0-9A-F]{6}$")


def _parse_atc_alphanumeric(identifier: str | None) -> tuple[str, str] | None:
    """2026-10-20 (bellwether batch): an alphanumeric ATC callsign -- the 3-letter
    ICAO designator, a digit, then letters (UAE3MX, DLH3Y, AFR50N, QTR18F) -- as
    ('UAE', '3MX'), the split the FDPS writer stores (airline = cs[:3]). These are
    the forms the codeshare_map atc_callsign links point at. A 6-character all-hex
    string is a Mode-S address, never a callsign."""
    cs = (identifier or "").strip().upper().replace(" ", "")
    if _HEX6_RE.match(cs):
        return None
    m = _ATC_ALNUM_RE.match(cs)
    if not m or m.group(2).isdigit():
        return None
    return m.group(1), m.group(2)


def parse_flight_ident(identifier: str | None) -> tuple[str, str] | None:
    """('GJS', '4520') from GJS4520 / G74520 / 'UA 4520' (IATA carriers go to
    ICAO through common.airline_codes). None for tails, blanks, and 2-char
    carriers we do not know -- never a guess."""
    parts = split_callsign(identifier)
    if not parts:
        return _parse_atc_alphanumeric(identifier)
    prefix, num = parts
    if len(prefix) == 3 and prefix.isalpha():
        return prefix, num
    icao = IATA_TO_ICAO.get(prefix)
    return (icao, num) if icao else None


def operating_identities_detail(identifier: str | None, origin: str | None = None,
                                destination: str | None = None) -> list[tuple[str, str, str]]:
    """(carrier, number, relation) for the identifier itself ('identifier', ICAO
    form) plus every CURRENT codeshare_map link whose stored route does not
    contradict the given origin/destination: 'codeshare' (marketing -> operating
    flight) and, since 2026-10-20 (pg_schema/0082), 'atc_callsign' (public number
    -> the ATC callsign it is filed under: EK231 -> ('UAE', '3MX')). UAL4520 with a
    GJS4520 DCA-ORD row and an RPA4520 EWR-DCA row resolves to GJS4520 only when
    the watch says DCA-ORD. Retired links (relinked, kept as history) never count.
    DB errors (no codeshare_map table in a partial test DB) degrade to the
    identifier alone."""
    parsed = parse_flight_ident(identifier)
    if not parsed:
        return []
    out: list[tuple[str, str, str]] = [(parsed[0], parsed[1], "identifier")]
    seen = {parsed}
    carrier, num = parsed
    try:
        from common import db
        rows = db.get_codeshare_mapping_by_marketing(carrier, num)
    except Exception as e:
        log.debug("flight_instance: codeshare lookup %s%s failed (non-fatal): %s", carrier, num, e)
        rows = []
    for r in rows:
        op_c = (r.get("operating_carrier") or "").upper()
        if not op_c:
            continue
        if airports_agree(r.get("origin"), origin) is False:
            continue
        if airports_agree(r.get("destination"), destination) is False:
            continue
        op_n = (r.get("operating_flight_num") or num).upper()
        if op_n.isdigit():
            op_n = op_n.lstrip("0") or "0"
        if (op_c, op_n) not in seen:
            seen.add((op_c, op_n))
            out.append((op_c, op_n, r.get("relation") or "codeshare"))
    return out


def operating_identities(identifier: str | None, origin: str | None = None,
                         destination: str | None = None) -> list[tuple[str, str]]:
    """(carrier, number) pairs of operating_identities_detail: the identifier itself
    (ICAO form) plus its current, route-filtered codeshare and ATC-callsign links."""
    return [(c, n) for c, n, _rel in operating_identities_detail(identifier, origin, destination)]


# ── Instance key + instance ──────────────────────────────────────────────────

@dataclass(frozen=True)
class FlightInstanceKey:
    """Operating carrier + number + origin + destination + scheduled UTC date.
    Airports are stored normalized (norm_airport); any component may be None
    when the source did not say, and same_instance() treats None as
    "cannot prove", never as a wildcard."""
    carrier: str
    flight_num: str
    origin: str | None
    destination: str | None
    sched_date: str | None

    @property
    def callsign(self) -> str:
        return f"{self.carrier}{self.flight_num}"

    @classmethod
    def make(cls, identifier: str | None, origin: str | None = None,
             destination: str | None = None, sched=None) -> "FlightInstanceKey | None":
        parsed = parse_flight_ident(identifier)
        if not parsed:
            return None
        return cls(parsed[0], parsed[1], norm_airport(origin), norm_airport(destination),
                   utc_date(sched))

    def __str__(self) -> str:
        return (f"{self.callsign} {self.origin or '?'}-{self.destination or '?'} "
                f"{self.sched_date or '?'}")


@dataclass
class FlightInstance:
    """One leg. Times are epoch seconds (None = unknown). `act_*` come from
    TFMS airline-posted OOOI times or the caller; this module only reads
    them."""
    key: FlightInstanceKey
    hex: str | None = None
    registration: str | None = None
    sched_dep: float | None = None
    sched_arr: float | None = None
    est_dep: float | None = None
    est_arr: float | None = None
    act_dep: float | None = None
    act_arr: float | None = None
    status: str | None = None
    gufi: str | None = None
    sources: tuple = ()
    marketing: str | None = None
    updated_at: float | None = None
    aircraft_type: str | None = None   # ICAO type designator (FDPS), for the turn class

    @property
    def best_dep(self) -> float | None:
        return self.act_dep or self.est_dep or self.sched_dep

    @property
    def best_arr(self) -> float | None:
        return self.act_arr or self.est_arr or self.sched_arr

    @property
    def cancelled(self) -> bool:
        return (self.status or "").lower() in ("cancelled", "canceled")

    @property
    def diverted(self) -> bool:
        return "divert" in (self.status or "").lower()


def _key_of(x) -> FlightInstanceKey | None:
    if isinstance(x, FlightInstance):
        return x.key
    if isinstance(x, FlightInstanceKey):
        return x
    return None


def same_instance(a, b) -> bool:
    """Strict: same operating carrier + number, both airports positively
    agree, and both scheduled dates known and equal. Accepts FlightInstance
    or FlightInstanceKey. Unknown components never prove sameness."""
    ka, kb = _key_of(a), _key_of(b)
    if ka is None or kb is None:
        return False
    if (ka.carrier, ka.flight_num) != (kb.carrier, kb.flight_num):
        return False
    if airports_agree(ka.origin, kb.origin) is not True:
        return False
    if airports_agree(ka.destination, kb.destination) is not True:
        return False
    return bool(ka.sched_date and kb.sched_date and ka.sched_date == kb.sched_date)


def instance_from_tfms_row(row: dict, marketing: str | None = None) -> FlightInstance | None:
    """flight_ooooi_times row -> instance. original_departure is TFMS's
    scheduled departure, so this source carries the date the key needs."""
    ident = row.get("callsign") or f"{row.get('airline') or ''}{row.get('flight_num') or ''}"
    sched_dep = to_epoch(row.get("original_departure"))
    key = FlightInstanceKey.make(ident, row.get("origin"), row.get("destination"), sched_dep)
    if key is None:
        return None
    return FlightInstance(
        key=key, sched_dep=sched_dep, sched_arr=to_epoch(row.get("original_arrival")),
        act_dep=to_epoch(row.get("airline_off_time")) or to_epoch(row.get("airline_out_time")),
        act_arr=to_epoch(row.get("airline_on_time")) or to_epoch(row.get("airline_in_time")),
        status=row.get("flight_status"), gufi=row.get("gufi"), sources=("tfms",),
        marketing=marketing, updated_at=to_epoch(row.get("updated_at")))


def instance_from_flight_event(row: dict, marketing: str | None = None) -> FlightInstance | None:
    """flight_events (FDPS) row -> instance, with hex/registration parsed from
    this row's OWN <flight> element (raw_json is a batch; see
    db._find_flight_element). The FDPS writer stores departure_time as NULL
    today, so FDPS-only instances usually carry no scheduled date."""
    from common import db
    airline = (row.get("airline") or "").upper()
    num = row.get("flight_num") or ""
    key = FlightInstanceKey.make(f"{airline}{num}", row.get("origin"), row.get("destination"),
                                 row.get("departure_time"))
    if key is None:
        return None
    hex_id = reg = None
    try:
        flight = db._find_flight_element(row.get("raw_json"), f"{airline}{num}")
        hex_id, reg = db._extract_aircraft_hex_registration(flight)
    except Exception as e:
        log.debug("flight_instance: hex parse failed for %s: %s", row.get("flight_id"), e)
    return FlightInstance(
        key=key, hex=(hex_id or "").lower() or None, registration=reg,
        sched_dep=to_epoch(row.get("departure_time")), est_arr=to_epoch(row.get("arrival_time")),
        status=row.get("status"), gufi=row.get("flight_id"), sources=("fdps",),
        marketing=marketing, updated_at=to_epoch(row.get("updated_at")),
        aircraft_type=(row.get("aircraft_type") or "").strip().upper() or None)


def _merge(into: FlightInstance, other: FlightInstance) -> None:
    """Fill gaps in `into` from `other` (same GUFI). Never overwrites."""
    for name in ("hex", "registration", "sched_dep", "sched_arr", "est_dep", "est_arr",
                 "act_dep", "act_arr", "status"):
        if getattr(into, name) is None and getattr(other, name) is not None:
            setattr(into, name, getattr(other, name))
    into.sources = tuple(dict.fromkeys(into.sources + other.sources))


def _rows(sql: str, params: tuple) -> list[dict]:
    from common import db
    try:
        with db.conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]
    except Exception as e:
        log.debug("flight_instance: query failed (non-fatal): %s", e)
        return []


def resolve(identifier: str, origin: str | None = None, destination: str | None = None,
            sched_date=None, *, hex_id: str | None = None,
            limit_per_source: int = 50) -> list[FlightInstance]:
    """Candidate instances for (identifier, route, date), across the
    identifier itself and its route-filtered codeshare operating flights.
    Rows whose route or date positively contradict the request are dropped;
    rows that merely do not say are kept (so the caller can see an FDPS plan
    with no date). Same-GUFI TFMS + FDPS rows merge into one instance.
    Sorted by scheduled departure (unknown last)."""
    want_date = utc_date(sched_date)
    want_hex = (hex_id or "").lower().strip() or None
    marketing = None
    parsed = parse_flight_ident(identifier)
    if parsed:
        marketing = f"{parsed[0]}{parsed[1]}"
    by_gufi: dict[str, FlightInstance] = {}
    loose: list[FlightInstance] = []

    for carrier, num in operating_identities(identifier, origin, destination):
        mk = marketing if f"{carrier}{num}" != marketing else None
        found: list[FlightInstance] = []
        if re.fullmatch(r"\d+[A-Z]?", num):
            tfms_sql, tfms_args = ("SELECT * FROM flight_ooooi_times WHERE airline = ? AND flight_num = ? "
                                   "ORDER BY updated_at DESC LIMIT ?", (carrier, num, limit_per_source))
        else:
            # 2026-10-20: db.upsert_flight_ooooi stores an alphanumeric ATC callsign
            # (UAE3MX) with airline/flight_num NULL; the callsign column has it.
            tfms_sql, tfms_args = ("SELECT * FROM flight_ooooi_times WHERE callsign = ? "
                                   "ORDER BY updated_at DESC LIMIT ?", (f"{carrier}{num}", limit_per_source))
        for row in _rows(tfms_sql, tfms_args):
            inst = instance_from_tfms_row(row, mk)
            if inst:
                found.append(inst)
        for row in _rows("SELECT * FROM flight_events WHERE airline = ? AND flight_num = ? "
                         "ORDER BY updated_at DESC LIMIT ?", (carrier, num, limit_per_source)):
            inst = instance_from_flight_event(row, mk)
            if inst:
                found.append(inst)
        # merge same-GUFI rows FIRST, so an undated FDPS row inherits its
        # TFMS twin's date before the date filter sees it
        for inst in found:
            if inst.gufi and inst.gufi in by_gufi:
                _merge(by_gufi[inst.gufi], inst)
            elif inst.gufi:
                by_gufi[inst.gufi] = inst
            else:
                loose.append(inst)

    out = []
    for inst in list(by_gufi.values()) + loose:
        if inst.key.sched_date is None and inst.sched_dep is not None:
            inst.key = FlightInstanceKey(inst.key.carrier, inst.key.flight_num, inst.key.origin,
                                         inst.key.destination, utc_date(inst.sched_dep))
        if airports_agree(inst.key.origin, origin) is False:
            continue
        if airports_agree(inst.key.destination, destination) is False:
            continue
        if want_date and inst.key.sched_date and inst.key.sched_date != want_date:
            continue
        if want_hex and inst.hex and inst.hex != want_hex:
            continue
        out.append(inst)
    out.sort(key=lambda i: (i.sched_dep is None, i.sched_dep or 0))
    return out


def instances_for_hex(hex_id: str, window_h: float = 30.0) -> list[FlightInstance]:
    """Every FDPS-filed instance (flight_events, updated within window_h)
    whose OWN <flight> element carries this hex. The LIKE is only a
    prefilter -- raw_json batches bundle unrelated flights, so each hit is
    re-parsed and kept only if its own aircraft address matches. ~20 s live on
    47k rows: periodic callers go through poller.main._hex_instances (10-min
    per-hex cache), never per tick."""
    hx = (hex_id or "").strip().upper()
    if not re.fullmatch(r"[0-9A-F]{6}", hx):
        return []
    cutoff = time.time() - window_h * 3600.0
    out = []
    for row in _rows("SELECT * FROM flight_events WHERE updated_at > ? AND raw_json LIKE ?",
                     (cutoff, f"%{hx}%")):
        inst = instance_from_flight_event(row)
        if inst and inst.hex == hx.lower():
            out.append(inst)
    return out


def previous_leg(candidates: Iterable[FlightInstance], watched: FlightInstance,
                 exclude_gufi: str | None = None) -> FlightInstance | None:
    """The candidate tail's leg INTO the watched origin before the watched
    departure: latest-arriving instance whose destination agrees with the
    watched origin (a diverted inbound therefore is not found here -- that
    is the guard's job to report). Candidates with no arrival time are
    considered only if nothing timed qualifies."""
    w_dep = watched.best_dep
    timed, untimed = [], []
    for inst in candidates:
        if exclude_gufi and inst.gufi == exclude_gufi:
            continue
        if same_instance(inst, watched):
            continue
        if airports_agree(inst.key.destination, watched.key.origin) is not True:
            continue
        arr = inst.best_arr
        if arr is None:
            untimed.append(inst)
        elif w_dep is None or arr <= w_dep + 6 * 3600:
            timed.append(inst)
    if timed:
        return max(timed, key=lambda i: i.best_arr)
    return untimed[0] if untimed else None


# ── Registry helpers (read-only) ─────────────────────────────────────────────

def registration_for_hex(hex_id: str | None) -> str | None:
    """Tail for a Mode-S hex from the local FAA registry (N-number), then
    OpenSky. None if neither knows it or the lookup errors."""
    if not hex_id:
        return None
    from common import db
    try:
        faa = db.faa_lookup_by_hex(hex_id)
        if faa and faa.get("n_number"):
            n = faa["n_number"].upper()
            return n if n.startswith("N") else f"N{n}"
    except Exception as e:
        log.debug("flight_instance: FAA hex lookup failed: %s", e)
    try:
        osky = db.opensky_lookup_by_hex(hex_id)
        if osky and osky.get("registration"):
            return osky["registration"].upper()
    except Exception as e:
        log.debug("flight_instance: OpenSky hex lookup failed: %s", e)
    return None


def hex_for_registration(registration: str | None) -> str | None:
    """Mode-S hex for a tail (FAA authoritative for N-numbers, OpenSky
    fallback) -- same order add_flight_watchlist already uses."""
    if not registration:
        return None
    from common import db
    try:
        faa = db.faa_lookup_by_n_number(registration)
        if faa and faa.get("mode_s_hex"):
            return faa["mode_s_hex"].lower()
    except Exception as e:
        log.debug("flight_instance: FAA reg lookup failed: %s", e)
    try:
        osky = db.opensky_lookup_by_registration(registration)
        if osky and osky.get("icao24"):
            return osky["icao24"].lower()
    except Exception as e:
        log.debug("flight_instance: OpenSky reg lookup failed: %s", e)
    return None


# ── Watch matcher (TFMS / TBFM) ──────────────────────────────────────────────

# A watch's scheduled departure and a message's scheduled departure (TFMS
# igtd) further apart than this are different legs of a reused number.
WATCH_SCHED_TOLERANCE_H = 3.0

_ALIAS_TTL_S = 300.0
_alias_cache: dict[tuple, tuple[float, dict]] = {}


def clear_alias_cache() -> None:
    _alias_cache.clear()


def watch_alias_relations(entry: dict) -> dict[str, str]:
    """ICAO callsign -> how this watch answers to it: 'identifier' (its own
    number, ICAO form: EK231 -> UAE231), 'codeshare' (route-filtered operating
    flight) or 'atc_callsign' (route-filtered current ATC-callsign link, 2026-10-20:
    EK231 -> UAE3MX). Cached per (identifier, route) for five minutes so the
    national TFMS stream costs a dict probe, not a codeshare query per message."""
    ident = (entry.get("identifier") or "").upper().strip()
    ck = (ident, norm_airport(entry.get("origin")), norm_airport(entry.get("destination")))
    now = time.monotonic()
    hit = _alias_cache.get(ck)
    if hit and hit[0] > now:
        return hit[1]
    rels = {f"{c}{n}": rel for c, n, rel in
            operating_identities_detail(ident, entry.get("origin"), entry.get("destination"))}
    _alias_cache[ck] = (now + _ALIAS_TTL_S, rels)
    return rels


def watch_operating_aliases(entry: dict) -> frozenset:
    """ICAO callsigns a watch answers to: its own identifier plus its
    route-filtered codeshare operating flights and ATC-callsign links."""
    return frozenset(watch_alias_relations(entry))


# 2026-10-20 (bellwether batch): a match through the lookaside (a codeshare's
# operating callsign or an ATC-assigned alphanumeric) must be PROVEN on the watch's
# route -- at least one message airport positively agrees -- not merely
# uncontradicted. A recycled alphanumeric (W26 season change 2026-10-25) or a
# codeshare's other leg would otherwise advance the watch from a message that
# carries no airports.
LOOKASIDE_RELATIONS = frozenset({"codeshare", "atc_callsign"})


def _identity_matches(entry: dict, ident_upper: str | None, ident_icao: str | None,
                      hex_id: str | None) -> str | None:
    """How a watch entry answers to this message, or None: 'identifier', 'hex',
    'codeshare' or 'atc_callsign'. The raw upper-cased callsign is probed too:
    an alphanumeric like UAE3MX is not flight-number shaped, so it has no ICAO
    normal form."""
    e_ident = (entry.get("identifier") or "").upper().strip()
    if ident_upper and e_ident == ident_upper:
        return "identifier"
    if hex_id and (entry.get("hex_id") or "").lower().strip() == hex_id:
        return "hex"
    if ident_icao or ident_upper:
        rels = watch_alias_relations(entry)
        for cand in (ident_icao, ident_upper):
            if cand and cand in rels:
                return rels[cand]
    return None


def lookaside_route_unproven(entry: dict, origin: str | None, destination: str | None) -> bool:
    """True when neither message airport positively agrees with the watch's route;
    a lookaside (codeshare / ATC-callsign) match is then not trusted."""
    return not (airports_agree(entry.get("origin"), origin) is True
                or airports_agree(entry.get("destination"), destination) is True)


def is_leg_watch(entry: dict) -> bool:
    """2026-10-14 (swarm audit NB-02): leg/side checks apply only to a watch on ONE
    leg -- a flight-number identifier with both origin and destination. A tail or
    aircraft watch, or a permanent watch with only a home origin, follows the
    aircraft or number on every leg (callsign matching, as before 2026-10-13)."""
    return bool(parse_flight_ident(entry.get("identifier"))
                and norm_airport(entry.get("origin")) and norm_airport(entry.get("destination")))


def leg_rejection(entry: dict, origin: str | None, destination: str | None,
                  sched_dep=None, tolerance_h: float = WATCH_SCHED_TOLERANCE_H,
                  diversion_without_sched: bool = False) -> str | None:
    """Why a message for the right NUMBER is the wrong LEG for this watch,
    or None if nothing contradicts it.
      - cross-direction / other route: an airport positively disagrees;
      - other time of day: both scheduled departures known, > tolerance apart.
    One narrow exception: origin agrees and the scheduled departures
    positively agree, but the destination differs -- that is this leg
    diverting (FDPS/TFMS amend the destination), not another leg, and
    dropping it would lose the arrival at the alternate."""
    o_ok = airports_agree(entry.get("origin"), origin)
    d_ok = airports_agree(entry.get("destination"), destination)
    w_ep, m_ep = to_epoch(entry.get("scheduled_departure")), to_epoch(sched_dep)
    sched_same = None
    if w_ep is not None and m_ep is not None:
        sched_same = abs(w_ep - m_ep) <= tolerance_h * 3600.0
    if o_ok is False:
        if airports_agree(entry.get("origin"), destination) and \
                airports_agree(entry.get("destination"), origin):
            return "cross_direction"
        return "origin_mismatch"
    if sched_same is False:
        return "other_time_of_day"
    if d_ok is False:
        # Same origin and same schedule: this leg, destination amended (a diversion).
        # 2026-10-14 (swarm audit NB-03): callers whose messages never carry a schedule
        # (FDPS plans, SMES arrivals on their own route) pass diversion_without_sched,
        # so "schedule unknown" does not turn a diversion into another leg. TFMS stays strict.
        if o_ok is True and (sched_same is True or (diversion_without_sched and sched_same is None)):
            return None
        return "destination_mismatch"
    return None


def match_watch_entry(entries: Iterable[dict], identifier: str | None, *,
                      origin: str | None = None, destination: str | None = None,
                      hex_id: str | None = None, sched_dep=None,
                      tolerance_h: float = WATCH_SCHED_TOLERANCE_H,
                      diversion_without_sched: bool = False) -> dict | None:
    """First flight watch that (a) answers to this identifier -- exactly, by
    ICAO normalization, through a route-filtered codeshare_map operating
    callsign, or by hex -- AND (b) is not contradicted by the message's
    departure/arrival airports or scheduled departure (leg_rejection). A
    same-number shuttle's other leg fails (b) and never advances the watch."""
    ident_upper = (identifier or "").upper().strip() or None
    hx = (hex_id or "").lower().strip() or None
    if not ident_upper and not hx:
        return None
    parsed = parse_flight_ident(ident_upper)
    ident_icao = f"{parsed[0]}{parsed[1]}" if parsed else None
    for entry in entries:
        how = _identity_matches(entry, ident_upper, ident_icao, hx)
        if not how:
            continue
        why = (leg_rejection(entry, origin, destination, sched_dep, tolerance_h, diversion_without_sched)
               if is_leg_watch(entry) else None)
        if not why and how in LOOKASIDE_RELATIONS and lookaside_route_unproven(entry, origin, destination):
            why = "lookaside_route_unproven"
        if why:
            log.debug("watch %s: %s matched by %s but rejected (%s; msg %s-%s sched %s)",
                      entry.get("id"), ident_upper or hx, how, why, origin, destination, sched_dep)
            continue
        return entry
    return None


SURFACE_DEPARTURE_EVENTS = ("spotout", "off")
SURFACE_ARRIVAL_EVENTS = ("on", "runwayout", "spotin")
_SURFACE_ARRIVAL_MAX_H = 20.0     # longest leg + delay: an arrival this long after the scheduled departure is another day


def surface_leg_rejection(entry: dict, airport: str | None, event: str | None, event_time,
                          dep_airport: str | None = None, dest_airport: str | None = None) -> str | None:
    """Why an ASDE-X surface event for the right NUMBER is the wrong LEG for
    this watch, or None. SMES sees one airport per message, which for a
    shuttle or an out-and-back is both legs' endpoint, so the side and the
    time decide:
      - the message's own route (enhancedData), when present, must not
        contradict the watch (leg_rejection);
      - a departure event (spotout/off) must be at the watch's origin, an
        arrival event (on/runwayout/spotin) at its destination;
      - with a scheduled departure on the watch: a departure event inside
        [sched - 2 h, sched + 6 h] (INSTANCE-v1's window), an arrival event
        inside [sched - 2 h, sched + 20 h]."""
    ev = (event or "").lower()
    if dep_airport or dest_airport:
        why = leg_rejection(entry, dep_airport, dest_airport,
                            diversion_without_sched=ev in SURFACE_ARRIVAL_EVENTS)
        if why:
            return why
    if ev in SURFACE_DEPARTURE_EVENTS:
        side_ok, after_h = airports_agree(entry.get("origin"), airport), 6.0
    elif ev in SURFACE_ARRIVAL_EVENTS:
        side_ok, after_h = airports_agree(entry.get("destination"), airport), _SURFACE_ARRIVAL_MAX_H
    else:
        return None
    if side_ok is False:
        if ev in SURFACE_ARRIVAL_EVENTS and airports_agree(entry.get("origin"), dep_airport) is True:
            pass    # arrival at an alternate on this leg's own route (diversion, NB-03)
        else:
            return "wrong_side_airport"
    sched, t = to_epoch(entry.get("scheduled_departure")), to_epoch(event_time)
    if sched is not None and t is not None and not (sched - 2 * 3600 <= t <= sched + after_h * 3600):
        return "outside_instance_window"
    return None


def match_watch_surface_event(entries: Iterable[dict], callsign: str | None, *, airport: str | None,
                              event: str | None, event_time=None, dep_airport: str | None = None,
                              dest_airport: str | None = None) -> dict | None:
    """First flight watch that answers to this callsign (exactly, by ICAO
    normalization or a route-filtered codeshare operating callsign) and is
    not contradicted by surface_leg_rejection."""
    ident_upper = (callsign or "").upper().strip() or None
    if not ident_upper:
        return None
    parsed = parse_flight_ident(ident_upper)
    ident_icao = f"{parsed[0]}{parsed[1]}" if parsed else None
    for entry in entries:
        how = _identity_matches(entry, ident_upper, ident_icao, None)
        if not how:
            continue
        why = (surface_leg_rejection(entry, airport, event, event_time, dep_airport, dest_airport)
               if is_leg_watch(entry) else None)
        if not why and how in LOOKASIDE_RELATIONS and lookaside_route_unproven(entry, dep_airport, dest_airport):
            # SMES sees one airport: a departure event there proves the origin,
            # an arrival event the destination.
            ev = (event or "").lower()
            side = (entry.get("origin") if ev in SURFACE_DEPARTURE_EVENTS
                    else entry.get("destination") if ev in SURFACE_ARRIVAL_EVENTS else None)
            if airports_agree(side, airport) is not True:
                why = "lookaside_route_unproven"
        if why:
            log.debug("watch %s: %s matched by %s but surface event rejected (%s; %s at %s %s)",
                      entry.get("id"), ident_upper, how, why, event, airport, event_time)
            continue
        return entry
    return None


# ── Time-fidelity pairing ────────────────────────────────────────────────────

def event_key(event: dict) -> FlightInstanceKey | None:
    """Instance key of a time-fidelity event dict: identifier|callsign,
    origin, destination, sched_dep|scheduled_departure."""
    return FlightInstanceKey.make(
        event.get("identifier") or event.get("callsign"),
        event.get("origin"), event.get("destination"),
        event.get("sched_dep") if event.get("sched_dep") is not None
        else event.get("scheduled_departure"))


def pair_rejection(a: dict, b: dict, sched_tolerance_min: float = 90.0) -> str | None:
    """None if a and b are the same instance (and, when both carry a phase,
    the same phase); else the reason: no_key, different_flight,
    cross_direction, different_route, no_sched, different_date,
    other_time_of_day, different_phase."""
    ka, kb = event_key(a), event_key(b)
    if ka is None or kb is None:
        return "no_key"
    if (ka.carrier, ka.flight_num) != (kb.carrier, kb.flight_num):
        return "different_flight"
    if airports_agree(ka.origin, kb.origin) is not True or \
            airports_agree(ka.destination, kb.destination) is not True:
        if airports_agree(ka.origin, kb.destination) and airports_agree(ka.destination, kb.origin):
            return "cross_direction"
        return "different_route"
    if not ka.sched_date or not kb.sched_date:
        return "no_sched"
    if ka.sched_date != kb.sched_date:
        return "different_date"
    ea = to_epoch(a.get("sched_dep") if a.get("sched_dep") is not None else a.get("scheduled_departure"))
    eb = to_epoch(b.get("sched_dep") if b.get("sched_dep") is not None else b.get("scheduled_departure"))
    if ea is not None and eb is not None and abs(ea - eb) > sched_tolerance_min * 60.0:
        return "other_time_of_day"
    pa, pb = (a.get("phase") or "").lower(), (b.get("phase") or "").lower()
    if pa and pb and pa != pb:
        return "different_phase"
    return None


_REJECT_RANK = ("different_phase", "other_time_of_day", "different_date", "cross_direction",
                "no_sched", "different_route", "different_flight", "no_key")


@dataclass
class PairingResult:
    pairs: list = field(default_factory=list)       # [(a, b)]
    rejected: list = field(default_factory=list)    # [(a, nearest_b_or_None, reason)]
    unpaired_b: list = field(default_factory=list)


def pair_events(reference: Iterable[dict], candidates: Iterable[dict], *,
                sched_tolerance_min: float = 90.0) -> PairingResult:
    """Pair each reference event with at most one candidate of the SAME
    instance (pair_rejection is None), nearest scheduled departure first;
    each candidate is used once. An unpaired reference event records the
    most informative reason it found (a same-number other-time-of-day leg
    outranks an unrelated flight)."""
    cands = list(candidates)
    used: set[int] = set()
    res = PairingResult()
    for a in reference:
        best_i, best_gap = None, None
        nearest, nearest_reason = None, None
        ea = to_epoch(a.get("sched_dep") if a.get("sched_dep") is not None else a.get("scheduled_departure"))
        for i, b in enumerate(cands):
            if i in used:
                continue
            why = pair_rejection(a, b, sched_tolerance_min)
            if why is None:
                eb = to_epoch(b.get("sched_dep") if b.get("sched_dep") is not None
                              else b.get("scheduled_departure"))
                gap = abs(ea - eb) if ea is not None and eb is not None else float("inf")
                if best_gap is None or gap < best_gap:
                    best_i, best_gap = i, gap
            elif nearest_reason is None or _REJECT_RANK.index(why) < _REJECT_RANK.index(nearest_reason):
                nearest, nearest_reason = b, why
        if best_i is not None:
            used.add(best_i)
            res.pairs.append((a, cands[best_i]))
        else:
            res.rejected.append((a, nearest, nearest_reason or "no_candidates"))
    res.unpaired_b = [b for i, b in enumerate(cands) if i not in used]
    return res


# ── Surface-event pairing: INSTANCE-v1 (time-fidelity checkpoint 2026-10-13) ─
# Pairs ASDE-X surface events (spotout/off at the origin, on/runwayout/spotin
# at the destination) with ONE airline instance. Never "nearest to the
# reported time" (that biases toward agreement): exactly one surviving
# candidate, or no pair. Rule text: docs/FLIGHT_INSTANCE_RESOLVER.md.

V1_DEPARTURE_EVENTS = ("spotout", "off")
V1_ARRIVAL_EVENTS = ("on", "runwayout", "spotin")
V1_WINDOW_BEFORE_S = 2 * 3600
V1_WINDOW_AFTER_S = 6 * 3600
V1_RAMP_EXIT_TO_OFF_MAX_MIN = 90
V1_RUNWAY_EXIT_TO_RAMP_ENTRY_MAX_MIN = 60


def _v1_side(kind: str) -> str | None:
    if kind in V1_DEPARTURE_EVENTS:
        return "departure"
    if kind in V1_ARRIVAL_EVENTS:
        return "arrival"
    return None


def _v1_fits(inst: dict, ev: dict, side: str) -> bool:
    """Same callsign, the relevant airport, inside the instance's window."""
    airport = norm_airport(inst.get("origin") if side == "departure" else inst.get("destination"))
    filed = to_epoch(inst.get("original_departure") if side == "departure" else inst.get("original_arrival"))
    t = to_epoch(ev.get("event_time"))
    if not airport or filed is None or t is None:
        return False
    if (ev.get("callsign") or "").strip().upper() != (inst.get("callsign") or "").strip().upper():
        return False
    if norm_airport(ev.get("airport")) != airport:
        return False
    return filed - V1_WINDOW_BEFORE_S <= t <= filed + V1_WINDOW_AFTER_S


def _v1_route(x: dict, o: str = "departure_airport", d: str = "destination_airport") -> tuple[str, str] | None:
    a, b = norm_airport(x.get(o)), norm_airport(x.get(d))
    return (a, b) if a and b else None


def v1_pair_surface(inst: dict, siblings: Iterable[dict], events: Iterable[dict],
                    kind: str) -> tuple[dict | None, str | None]:
    """(event, None) when exactly one `kind` event survives INSTANCE-v1 for
    `inst`; else (None, reason): unknown_kind, no_candidates, route_mismatch,
    sibling_ambiguous_routeknown, sibling_ambiguous_routeunknown,
    multi_candidate. `inst`/`siblings` are flight_ooooi_times-shaped dicts
    (callsign, origin, destination, original_departure, original_arrival);
    siblings are the OTHER instances of the same callsign."""
    side = _v1_side(kind)
    if side is None:
        return None, "unknown_kind"
    route = _v1_route(inst, "origin", "destination")
    sibs = [s for s in siblings if s is not inst]
    survivors, last_reason = [], "no_candidates"
    for ev in events:
        if (ev.get("event") or "").lower() != kind or not _v1_fits(inst, ev, side):
            continue
        r = _v1_route(ev)
        if r is not None:
            if r != route:
                last_reason = "route_mismatch"
                continue
            if any(_v1_fits(s, ev, side) and _v1_route(s, "origin", "destination") == r for s in sibs):
                last_reason = "sibling_ambiguous_routeknown"
                continue
        elif any(_v1_fits(s, ev, side) for s in sibs):
            last_reason = "sibling_ambiguous_routeunknown"
            continue
        survivors.append(ev)
    # the same event seen twice (slot table + append-only log) is one event
    distinct = {(norm_airport(e.get("airport")), to_epoch(e.get("event_time"))): e for e in survivors}
    if len(distinct) == 1:
        return next(iter(distinct.values())), None
    return None, ("multi_candidate" if distinct else last_reason)


def v1_pair_instance(inst: dict, siblings: Iterable[dict], events: Iterable[dict]) -> dict:
    """{kind: {"event": ev|None, "reason": str|None}} for every surface kind,
    with the sequence rules applied: ramp exit -> OFF must be 0 < d <= 90 min,
    runway exit -> ramp entry 0 < d <= 60 min. The ramp event of a failing
    pair is dropped (reason sequence_violation); the runway event is kept."""
    evs, sibs = list(events), list(siblings)
    out = {}
    for kind in V1_DEPARTURE_EVENTS + V1_ARRIVAL_EVENTS:
        ev, why = v1_pair_surface(inst, sibs, evs, kind)
        out[kind] = {"event": ev, "reason": why}

    def _seq(first: str, second: str, drop: str, max_min: float) -> None:
        a, b = out[first]["event"], out[second]["event"]
        if a is None or b is None:
            return
        d = (to_epoch(b.get("event_time")) - to_epoch(a.get("event_time"))) / 60.0
        if not (0 < d <= max_min):
            out[drop] = {"event": None, "reason": "sequence_violation"}

    _seq("spotout", "off", "spotout", V1_RAMP_EXIT_TO_OFF_MAX_MIN)
    _seq("runwayout", "spotin", "spotin", V1_RUNWAY_EXIT_TO_RAMP_ENTRY_MAX_MIN)
    return out


# ── Turn times ───────────────────────────────────────────────────────────────

# Minimum scheduled turn by airframe class, minutes. Env override per class:
# CTDC_MIN_TURN_MIN_NARROWBODY / _REGIONAL / _WIDEBODY (clamped 10-240).
DEFAULT_MIN_TURN_MIN = {"narrowbody": 35, "regional": 30, "widebody": 75}


# ICAO type-designator prefixes. Anything not listed is narrowbody (the
# conservative middle: 35 min). Regional = CRJ/ERJ/E-jet/turboprop shapes.
_WIDEBODY_PREFIXES = ("A30", "A31", "A33", "A34", "A35", "A38", "B74", "B76", "B77", "B78", "MD11", "IL96", "IL86")
_REGIONAL_PREFIXES = ("CRJ", "E13", "E14", "E17", "E75", "E19", "E29", "DH8", "AT4", "AT7", "SF34", "B190", "J328")


def aircraft_class_for_type(icao_type: str | None) -> str:
    """'widebody' | 'regional' | 'narrowbody' from an ICAO type designator."""
    t = (icao_type or "").strip().upper()
    if t.startswith(_WIDEBODY_PREFIXES) and t not in ("A318", "A319"):
        return "widebody"
    if t.startswith(_REGIONAL_PREFIXES):
        return "regional"
    return "narrowbody"


def min_turn_minutes(aircraft_class: str = "narrowbody") -> int:
    cls = (aircraft_class or "narrowbody").lower()
    base = DEFAULT_MIN_TURN_MIN.get(cls, DEFAULT_MIN_TURN_MIN["narrowbody"])
    raw = os.environ.get(f"CTDC_MIN_TURN_MIN_{cls.upper()}")
    if raw:
        try:
            return max(10, min(240, int(raw)))
        except ValueError:
            pass
    return base


# ── Tail-change guard ────────────────────────────────────────────────────────

# Sources allowed to repoint a watch's hex on their own: SWIM only. "fdps" =
# the FAA flight plan (aircraftDescription / ADS-B address on the plan);
# "tdes" (2026-10-14, operator) = the plan linked to this flight's TDES
# departure clearance at the origin (tdes_clearance_plan); "tbfm"/"tfms" =
# those SWIM feeds showing the tail on this flight. FIDS is NOT a tail source
# (operator 2026-10-14: "strictly TDES and FDPS based" -- MWAA FIDS is gate and
# baggage enrichment only). Anything else (FIDS, airplanes.live, OpenSky,
# AeroAPI, any tracker) can corroborate but never repoints alone.
AUTHORITATIVE_TAIL_SOURCES = frozenset({"fdps", "tdes", "tbfm", "tfms"})
# 2026-10-13 (operator: "strict, based off an FDPS, TBFM or TFMS that shows that flight"):
# a SWIM source showing the candidate tail ON THE WATCHED FLIGHT is itself the proof, so rule
# (c) (show the previous leg reaches the origin) is not required for these -- FDPS coverage
# here is DC-area only and an inbound's previous leg is usually not on file. Rule (b) still
# applies. FIDS is never a tail source (2026-10-14), so with SWIM-only sources (c) is currently unreachable. There is no "unverified inbound" acceptance.
SWIM_TAIL_SOURCES = frozenset({"fdps", "tdes", "tbfm", "tfms"})


def tdes_clearance_plan(callsigns: Iterable[str], origin: str | None, sched_dep,
                        window_h: float = 3.0) -> FlightInstance | None:
    """The FAA plan behind this flight's TDES departure clearance at `origin`
    (TDES covers DCA/IAD/BWI among others), as an instance carrying the plan's
    hex/registration -- or None. TDES has no tail of its own: its sfdps_gufi is
    the FAA-internal GUFI that the FDPS plan carries in supplementalData
    (FDPS_GUFI), so the link is that string inside the plan's raw message,
    for the same callsign. Clearances outside [sched - window_h, sched +
    window_h] belong to another instance and are ignored; more than one
    distinct linked plan -> None (never pick one)."""
    o = norm_airport(origin)
    sched = to_epoch(sched_dep)
    if not o or sched is None:
        return None
    cs = sorted({c.upper() for c in callsigns if c})
    if not cs:
        return None
    clearances = _rows(
        f"SELECT callsign, event_time, sfdps_gufi FROM tdes_departure_events WHERE airport IN (?, ?) "
        f"AND callsign IN ({','.join('?' * len(cs))})", (o, "K" + o, *cs))
    plans: dict[str, FlightInstance] = {}
    for t in clearances:
        te = to_epoch(t.get("event_time"))
        g = (t.get("sfdps_gufi") or "").strip()
        if te is None or not g or abs(te - sched) > window_h * 3600.0:
            continue
        parsed = parse_flight_ident(t["callsign"])
        if not parsed:
            continue
        for row in _rows("SELECT * FROM flight_events WHERE airline = ? AND flight_num = ? AND updated_at > ?",
                         (parsed[0], parsed[1], sched - 36 * 3600.0)):
            if g in (row.get("raw_json") or ""):
                inst = instance_from_flight_event(row)
                if inst is not None and inst.hex:
                    plans[inst.gufi or g] = inst
    return next(iter(plans.values())) if len(plans) == 1 else None

_ACTIVE_STATUSES = frozenset({"active", "airborne", "enroute", "en route", "departed"})


@dataclass(frozen=True)
class TailChangeDecision:
    accepted: bool
    changed: bool
    reason: str
    candidate_hex: str | None = None
    candidate_registration: str | None = None
    prior_hex: str | None = None

    def note(self, ident: str) -> str:
        tail = self.candidate_registration or self.candidate_hex or "?"
        if self.accepted:
            return f"{ident}: tail change accepted {self.prior_hex or '?'} -> {tail} ({self.reason})"
        return f"{ident}: tail change NOT accepted ({tail}): {self.reason}"


def _overlaps(inst: FlightInstance, watched: FlightInstance, turn_s: float) -> Optional[bool]:
    """Would flying `inst` stop the tail flying `watched`? None if `inst`
    carries no times at all."""
    i0, i1 = inst.best_dep, inst.best_arr
    if i0 is None and i1 is None:
        return None
    i0 = i0 if i0 is not None else i1
    i1 = i1 if i1 is not None else i0
    w0 = watched.best_dep
    w1 = watched.best_arr
    if w0 is None:
        return None
    w1 = w1 if w1 is not None else w0 + 3600.0
    return i0 < w1 and i1 > w0 - turn_s


def decide_tail_change(*, watched: FlightInstance, candidate_hex: str | None,
                       candidate_registration: str | None = None,
                       prior_hex: str | None = None, sources: Iterable[str] = (),
                       candidate_instances: Iterable[FlightInstance] = (),
                       previous: FlightInstance | None = None,
                       on_ground_at_origin: bool = False,
                       exclude_gufi: str | None = None,
                       min_turn_min: int | None = None,
                       late_tolerance_min: float = 0.0) -> TailChangeDecision:
    """Pure decision: may this watch repoint from prior_hex to candidate_hex?
    Accept only when ALL hold:
      (a) at least one source is authoritative (AUTHORITATIVE_TAIL_SOURCES);
      (b) the candidate tail is not filed on a different instance that
          overlaps the watched leg (the watched instance itself, its
          previous leg, cancelled legs and untimed non-active filings do
          not count);
      (c) its previous leg ends at the watched origin, not cancelled, and
          arrives at least min_turn before the watched departure (latest
          known estimate, else schedule; + late_tolerance_min) -- or the
          caller has positive evidence the tail is already on the ground at
          the origin. An unknown previous leg is a rejection: the rule is
          "show it gets there", not "assume it does"."""
    cand = (candidate_hex or "").lower().strip() or None
    prior = (prior_hex or "").lower().strip() or None
    srcs = tuple(sorted({(s or "").lower().strip() for s in sources if s}))

    def _no(reason: str, changed: bool = True) -> TailChangeDecision:
        return TailChangeDecision(False, changed, reason, cand, candidate_registration, prior)

    if not cand:
        return _no("no candidate hex", changed=False)
    if prior and cand == prior:
        return _no("same hex as the watch already has", changed=False)

    auth = [s for s in srcs if s in AUTHORITATIVE_TAIL_SOURCES]
    if not auth:
        return _no(f"source not authoritative ({', '.join(srcs) or 'none'}); needs FDPS, TBFM "
                   f"or TFMS showing this flight (or the TDES clearance's FDPS plan) -- never FIDS or a tracker alone")

    turn = min_turn_min if min_turn_min is not None else min_turn_minutes()
    turn_s = turn * 60.0
    for inst in candidate_instances:
        if exclude_gufi and inst.gufi == exclude_gufi:
            continue
        if same_instance(inst, watched):
            continue
        if previous is not None and (inst is previous or same_instance(inst, previous)
                                     or (inst.gufi and inst.gufi == previous.gufi)):
            continue
        if inst.cancelled:
            continue
        ov = _overlaps(inst, watched, turn_s)
        if ov is None:
            ov = (inst.status or "").lower() in _ACTIVE_STATUSES
        if ov:
            return _no(f"tail is filed on another instance {inst.key} "
                       f"({inst.status or 'status ?'}, dep {_fmt_utc(inst.best_dep)})")

    origin = watched.key.origin or "?"
    swim = [s for s in auth if s in SWIM_TAIL_SOURCES]
    if swim:
        why_c = f"{'+'.join(swim).upper()} shows this tail on this flight"
    elif on_ground_at_origin:
        why_c = f"tail on the ground at {origin}"
    else:
        if previous is None:
            return _no(f"previous leg of the tail unknown; cannot show it reaches {origin} in time")
        if previous.cancelled:
            return _no(f"previous leg {previous.key} is cancelled")
        if airports_agree(previous.key.destination, watched.key.origin) is not True:
            return _no(f"previous leg {previous.key} ends at {previous.key.destination or '?'}, "
                       f"not {origin}")
        arr = previous.best_arr
        w_dep = watched.est_dep or watched.sched_dep or watched.best_dep
        if arr is None:
            return _no(f"previous leg {previous.key} has no arrival time")
        if w_dep is None:
            return _no("watched leg has no departure time to measure the turn against")
        slack_min = (w_dep + late_tolerance_min * 60.0 - (arr + turn_s)) / 60.0
        if slack_min < 0:
            return _no(f"previous leg {previous.key} arrives {origin} {_fmt_utc(arr)}, "
                       f"{-slack_min:.0f} min short of a {turn}-min turn before "
                       f"{_fmt_utc(w_dep)}")
        why_c = (f"previous leg {previous.key} arrives {origin} {_fmt_utc(arr)}, "
                 f"{slack_min:.0f} min spare over a {turn}-min turn")

    return TailChangeDecision(True, True,
                              f"authoritative {'+'.join(auth)}; not filed on another instance; {why_c}",
                              cand, candidate_registration, prior)


# ── Inbound-aircraft tracker ("where's my inbound") ──────────────────────────

@dataclass(frozen=True)
class InboundAssessment:
    status: str                      # on_time | late_beyond_turn | at_origin | diverted | cancelled | unknown
    flags: tuple
    inbound: str | None              # str(inbound key)
    inbound_eta: float | None        # epoch, arrival at the watched origin
    min_turn_min: int
    ready_at: float | None           # inbound_eta + min turn
    scheduled_departure: float | None
    propagated_departure: float | None
    slack_min: float | None          # minutes spare (negative = late)
    summary: str


def assess_inbound(watched: FlightInstance, inbound: FlightInstance | None, *,
                   min_turn_min: int | None = None, aircraft_class: str = "narrowbody",
                   now: float | None = None) -> InboundAssessment:
    """Pure. Given the watched leg and the assigned tail's current instance
    (from resolve()/instances_for_hex()), estimate when the airplane can push
    for the watched leg. ETA preference: actual arrival > estimated > scheduled.
    Propagated departure = max(watched departure estimate-or-schedule, ETA +
    min turn). Flags: inbound_late_beyond_turn_buffer, inbound_diverted,
    inbound_cancelled, inbound_unknown, inbound_eta_unknown. Never asserts an
    OOOI phase for either leg."""
    turn = min_turn_min if min_turn_min is not None else min_turn_minutes(aircraft_class)
    turn_s = turn * 60.0
    w_sched = watched.sched_dep
    w_dep = watched.est_dep or watched.sched_dep
    ident = watched.key.callsign
    origin = watched.key.origin or "?"

    def _mk(status, flags, eta=None, ready=None, prop=None, slack=None, summary=""):
        return InboundAssessment(status, tuple(flags), str(inbound.key) if inbound else None,
                                 eta, turn, ready, w_sched, prop, slack, summary)

    if inbound is None:
        return _mk("unknown", ["inbound_unknown"], prop=w_dep,
                   summary=f"{ident}: inbound aircraft unknown")
    if inbound.cancelled:
        return _mk("cancelled", ["inbound_cancelled"], prop=None,
                   summary=f"{ident}: inbound {inbound.key} CANCELLED -- aircraft for "
                           f"the {origin} departure must come from elsewhere")
    if inbound.diverted or airports_agree(inbound.key.destination, watched.key.origin) is False:
        return _mk("diverted", ["inbound_diverted"], prop=None,
                   summary=f"{ident}: inbound {inbound.key} is not arriving {origin} "
                           f"({inbound.status or 'route'}); departure estimate unknown")

    eta = inbound.act_arr or inbound.est_arr or inbound.sched_arr
    if eta is None:
        return _mk("unknown", ["inbound_eta_unknown"], prop=w_dep,
                   summary=f"{ident}: inbound {inbound.key} has no arrival time yet")
    ready = eta + turn_s
    prop = max(w_dep, ready) if w_dep is not None else ready
    slack = (w_dep - ready) / 60.0 if w_dep is not None else None
    flags = []
    status = "at_origin" if inbound.act_arr is not None else "on_time"
    if slack is not None and slack < 0:
        flags.append("inbound_late_beyond_turn_buffer")
        status = "late_beyond_turn"
    late_txt = f"; departs ~{_fmt_utc(prop)} ({-slack:.0f} min late)" if slack is not None and slack < 0 else ""
    # 2026-10-20: say which time this is -- an actual, a SWIM estimate (FDPS
    # arrival_time, TBFM-derived), or only the schedule; never "ETA" for a schedule.
    word = ("arrived" if inbound.act_arr else "ETA (FDPS, TBFM-derived)" if inbound.est_arr
            else "scheduled arrival")
    summary = (f"{ident}: inbound {inbound.key} {word} "
               f"{origin} {_fmt_utc(eta)}, ready {_fmt_utc(ready)} ({turn}-min turn) vs "
               f"sched {_fmt_utc(w_sched)}{late_txt}")
    return _mk(status, flags, eta=eta, ready=ready, prop=prop, slack=slack, summary=summary)
