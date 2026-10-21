"""
common/callsign_lookaside.py -- learning the public number -> ATC callsign links
(2026-10-20, bellwether batch; operator decisions 2026-10-19/20).

Watch identifiers are PUBLIC flight numbers. Some carriers file an alphanumeric
ATC callsign instead (EK231 flies as UAE3MX, LH414 as DLH3Y); codeshare_map's
relation='atc_callsign' rows (pg_schema/0082, db.upsert_atc_callsign_link) let
every SWIM matcher resolve the watch through common.flight_instance. Two
learners keep those links current:

  learn_from_fids()   the existing FIDS/gate path (poller _check_flight_fids ->
                      common.airport_fids.match_leg). When a FIDS row for the
                      public number is matched to a SWIM-verified leg -- inside
                      that leg's own window, or as the only leg of the carrier on
                      the route -- and the leg's callsign differs from the public
                      number's ICAO form, the link is recorded: 'atc_callsign'
                      when the carrier is the same, 'codeshare' when it differs.
                      Only callsigns and the route are stored: never a FIDS time,
                      status or tail (the published time is read inside match_leg
                      to pick the row, as before, and nowhere else).
  relink_by_route_continuity()
                      daily (poller flight-cleanup skill). A linked ATC callsign
                      that has not flown its route for 2 consecutive UTC days,
                      with exactly ONE new callsign of the same carrier flying the
                      same route inside +/-30 min of the old callsign's typical
                      time on both days, is retired (kept as history, superseded_by)
                      and the new one linked (source 'route_continuity'). A
                      bellwether watch on that number gets one p3 push on
                      bellwether-alerts (shared.watch_topic) through
                      watchlist_event_hit.

Nothing here asserts a phase, stores a FIDS time, or logs an identifier beyond
the airline callsigns already in SWIM. LADD-listed identifiers are never linked
(fail closed, common.gate_assignments.ladd_blocked).
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from common import db
from common import flight_instance as fi

log = logging.getLogger(__name__)

CONTINUITY_SLOT_MIN = 30          # +/- minutes around the old callsign's typical time
CONTINUITY_GAP_DAYS = 2           # consecutive UTC days the old callsign must be absent
CONTINUITY_BASELINE_DAYS = 14     # history used for the typical time and "new"-ness
DISCOVERY_EVERY_S = 6 * 3600      # FIDS discovery attempts per watch side

_discovery_last: dict[tuple, float] = {}


# ── season ───────────────────────────────────────────────────────────────────

def _last_sunday(year: int, month: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def iata_season(day: date | None = None) -> str:
    """IATA scheduling season of a UTC date: S<yy> from the last Sunday of March,
    W<yy> from the last Sunday of October (W26 starts 2026-10-25)."""
    day = day or datetime.now(timezone.utc).date()
    s_start, w_start = _last_sunday(day.year, 3), _last_sunday(day.year, 10)
    if day < s_start:
        return f"W{(day.year - 1) % 100:02d}"
    if day < w_start:
        return f"S{day.year % 100:02d}"
    return f"W{day.year % 100:02d}"


# ── FIDS learner ─────────────────────────────────────────────────────────────

def fids_window(inst, side: str) -> tuple[float, float] | None:
    """The epoch window a FIDS row's published time must fall inside to belong to
    this SWIM/TFMS-verified leg (the one permitted use of a FIDS time). Same bounds
    the poller's gate path has used since 2026-10-18."""
    if inst is None:
        return None
    if side == "arrival":
        anchor = inst.sched_arr or inst.est_arr
        if anchor:
            return anchor - 2 * 3600, anchor + 8 * 3600
        return (inst.sched_dep, inst.sched_dep + 16 * 3600) if inst.sched_dep else None
    if inst.sched_dep:
        return inst.sched_dep - 2 * 3600, inst.sched_dep + 8 * 3600
    return None


def _ladd_blocked(idents: Iterable[str | None], hexes: Iterable[str | None] = ()) -> bool:
    from common import gate_assignments as ga
    return ga.ladd_blocked(idents, hexes)


def record_link(public_ident: str, op_carrier: str, op_num: str, origin: str | None,
                destination: str | None, source: str, hexes: Iterable[str | None] = ()) -> dict | None:
    """Store public number -> operating callsign on a route: an atc_callsign link
    when the carrier is the public number's own, a codeshare link otherwise. None
    when there is nothing to look aside to, or the identifiers are LADD-listed /
    the LADD lookup failed."""
    pub = db.split_public_number(public_ident)
    if not pub or not op_carrier or not op_num:
        return None
    op_carrier, op_num = op_carrier.upper(), str(op_num).upper()
    if (op_carrier, op_num) == pub:
        return None
    callsign = f"{op_carrier}{op_num}"
    if _ladd_blocked([public_ident, callsign], hexes):
        return None
    if op_carrier == pub[0]:
        res = db.upsert_atc_callsign_link(public_ident, callsign, origin, destination, source,
                                          season=iata_season())
    else:
        db.upsert_codeshare_mapping(pub[0], pub[1], op_carrier, op_num, db._norm_apt(origin),
                                    db._norm_apt(destination), source)
        res = {"action": "codeshare", "callsign": callsign}
    fi.clear_alias_cache()
    return res


def _route_candidates(carrier: str, origin: str, destination: str, sched_date: str | None,
                      exclude: set[str]) -> list:
    """SWIM-verified legs of `carrier` on origin-destination for the date (FDPS
    flight_events, merged with TFMS by GUFI through flight_instance.resolve), one
    per callsign, excluding the given callsigns."""
    since = time.time() - 36 * 3600
    o_set, d_set = _apt_forms(origin), _apt_forms(destination)
    try:
        rows = _route_rows("airline, flight_num, origin, destination", carrier, o_set, d_set,
                           since, time.time() + 60)
    except Exception as e:  # noqa: BLE001
        log.debug("lookaside: route candidates failed (non-fatal): %s", e)
        return []
    out = []
    seen = set()
    for r in rows:
        r = dict(r)
        if (r.get("origin") or "").upper() not in o_set or (r.get("destination") or "").upper() not in d_set:
            continue
        cs = f"{(r.get('airline') or '').upper()}{(r.get('flight_num') or '').upper()}"
        key = fi.parse_flight_ident(cs)
        if not key:
            continue
        norm = f"{key[0]}{key[1]}"
        if norm in exclude or norm in seen:
            continue
        seen.add(norm)
        insts = [i for i in fi.resolve(cs, origin, destination, None)
                 if sched_date is None or i.key.sched_date in (sched_date, None)]
        if insts:
            out.append(insts[0])
    return out


def _route_rows(cols: str, carrier: str, o_set: set[str], d_set: set[str],
                since_ep: float, until_ep: float) -> list[dict]:
    """flight_events rows of one carrier on one route in an update-time range
    (idx_flight_events_airline_num / _dest; no raw_json read)."""
    o_list, d_list = sorted(o_set), sorted(d_set)
    sql = (f"SELECT {cols} FROM flight_events WHERE airline = ? "
           f"AND origin IN ({','.join('?' * len(o_list))}) "
           f"AND destination IN ({','.join('?' * len(d_list))}) "
           "AND updated_at > ? AND updated_at <= ?")
    with db.conn() as c:
        return [dict(r) for r in c.execute(sql, (carrier, *o_list, *d_list, since_ep, until_ep)).fetchall()]


def _apt_forms(code: str | None) -> set[str]:
    c = (code or "").strip().upper()
    out = {c}
    if len(c) == 3 and c.isalpha():
        out.add("K" + c)
    elif len(c) == 4 and c.startswith("K"):
        out.add(c[1:])
    return out


def _board_public_numbers(airport_fids, airport: str, side: str, iata: str, icao: str) -> set[str]:
    """ICAO-form numbers of every FIDS row of this carrier on the board side --
    each is its own public flight, never another number's ATC callsign. Flight
    numbers only: nothing else is read from the rows."""
    data = airport_fids.get_data(airport.upper()) or {}
    board = data.get("arrivals" if side == "arrival" else "departures", []) or []
    out = set()
    for f in board:
        if str(f.get("IATA", "")).upper() != iata.upper():
            continue
        num = str(f.get("flightnumber", "")).strip().upper()
        if num.isdigit():
            num = num.lstrip("0") or "0"
        if num:
            out.add(f"{icao}{num}")
    return out


def learn_from_fids(entry: dict, ident: str, *, airport: str, side: str, iata: str,
                    flight_num: str, other: str | None, inst, row: dict | None,
                    sched_date: str | None) -> dict | None:
    """Called by the poller's FIDS gate step for one watch side (after match_leg).

    inst = the leg the watch already resolves to (through its own number or a
    current link). When FIDS matched it inside its window (leg_match
    'route_watch'), a link from the public number to inst's callsign is
    recorded/confirmed (source 'fids_leg_match'). With no verified leg (the number
    is filed under a callsign nothing links yet), at most every 6 h per watch
    side: the carrier's SWIM legs on the route are tried one by one; exactly one
    whose window (or, with no times, whose route uniqueness) picks the FIDS row
    is linked -- 'fids_route_unique' when it is the carrier's only leg on the
    route, 'fids_leg_match' otherwise. Ambiguous (two legs pick the row): nothing."""
    from common import airport_fids
    pub = db.split_public_number(ident)
    if not pub:
        return None
    origin, destination = (other, airport) if side == "arrival" else (airport, other)
    if not origin or not destination:
        return None
    own = f"{pub[0]}{pub[1]}"
    if inst is not None:
        if not row or row.get("leg_match") != "route_watch" or inst.key.callsign == own:
            return None
        return record_link(ident, inst.key.carrier, inst.key.flight_num, origin, destination,
                           "fids_leg_match", hexes=[inst.hex, entry.get("hex_id")])
    ck = (entry.get("id"), side)
    now = time.time()
    if now - _discovery_last.get(ck, 0.0) < DISCOVERY_EVERY_S:
        return None
    _discovery_last[ck] = now
    o_set, d_set = _apt_forms(origin), _apt_forms(destination)
    try:
        if any(fi.parse_flight_ident(f"{r['airline']}{r['flight_num']}") == pub
               for r in _route_rows("airline, flight_num", pub[0], o_set, d_set,
                                    now - 36 * 3600, now + 60)):
            return None     # filed under its own number: nothing to look aside to
    except Exception as e:  # noqa: BLE001
        log.debug("lookaside: own-number check failed (non-fatal): %s", e)
        return None
    # Another public flight of the carrier on the same board is not a candidate.
    exclude = {own} | _board_public_numbers(airport_fids, airport, side, iata, pub[0])
    cands = _route_candidates(pub[0], origin, destination, sched_date, exclude=exclude)
    if not cands:
        return None
    hits = []
    for c in cands:
        r = airport_fids.match_leg(airport, side, iata, flight_num, other, fids_window(c, side))
        if r is not None:
            hits.append((c, r.get("leg_match")))
    if len(hits) != 1:
        return None
    c, how = hits[0]
    if how == "route_only" and len(cands) != 1:
        return None
    source = "fids_route_unique" if len(cands) == 1 else "fids_leg_match"
    return record_link(ident, c.key.carrier, c.key.flight_num, origin, destination, source,
                       hexes=[c.hex, entry.get("hex_id")])


# ── route-continuity relink ──────────────────────────────────────────────────

def _minute_of_day(ep: float) -> int:
    t = datetime.fromtimestamp(ep, timezone.utc)
    return t.hour * 60 + t.minute


def _slot_distance(a: int, b: int) -> int:
    d = abs(a - b) % 1440
    return min(d, 1440 - d)


def route_observations(carrier: str, origin: str, destination: str,
                       since_ep: float, until_ep: float) -> list[tuple[str, str, int]]:
    """(normalised callsign, UTC date, minute of day) for every FDPS-filed leg of
    `carrier` on the route between the bounds (cancelled rows skipped). The leg's
    time is its FDPS estimated arrival, else its filed departure, else the row's
    own update time -- the same kind for every callsign on a route, so slots
    compare like with like."""
    o_set, d_set = _apt_forms(origin), _apt_forms(destination)
    rows = _route_rows("airline, flight_num, origin, destination, departure_time, arrival_time, "
                       "updated_at, status", carrier, o_set, d_set, since_ep, until_ep)
    out = []
    for r in rows:
        if (r.get("status") or "").lower() == "cancelled":
            continue
        parsed = fi.parse_flight_ident(f"{r.get('airline') or ''}{r.get('flight_num') or ''}")
        if not parsed:
            continue
        # FDPS writes departure_time NULL today and an estimated arrival_time
        # (checked 2026-10-20 on UAE3MX OMDB-KIAD): arrival first, so every row on
        # a route is compared on the same kind of time.
        ep = r.get("arrival_time") or r.get("departure_time") or r.get("updated_at")
        if not ep:
            continue
        ep = float(ep)
        out.append((f"{parsed[0]}{parsed[1]}", datetime.fromtimestamp(ep, timezone.utc).date().isoformat(),
                    _minute_of_day(ep)))
    return out


def _typical_minute(minutes: list[int]) -> int | None:
    if not minutes:
        return None
    ms = sorted(minutes)
    return ms[len(ms) // 2]


def continuity_decision(link: dict, observations: list[tuple[str, str, int]], today: date) -> dict | None:
    """Pure decision for one current atc_callsign link. observations cover
    [today - CONTINUITY_BASELINE_DAYS - CONTINUITY_GAP_DAYS, today). Returns
    {"new_callsign", "typical_min", "days"} when the link should move, else None.

    Rules: the old callsign is absent on each of the last CONTINUITY_GAP_DAYS
    complete UTC days and had a typical time in the baseline before them; exactly
    one callsign of the same carrier, NOT seen on the route in the baseline, flew
    the route inside +/-CONTINUITY_SLOT_MIN of that typical time on every gap day."""
    old = f"{link['operating_carrier']}{link['operating_flight_num']}"
    gap_days = [(today - timedelta(days=i)).isoformat() for i in range(1, CONTINUITY_GAP_DAYS + 1)]
    baseline = [(cs, d, m) for cs, d, m in observations if d not in gap_days]
    gap = [(cs, d, m) for cs, d, m in observations if d in gap_days]
    if any(cs == old for cs, _d, _m in gap):
        return None
    typical = _typical_minute([m for cs, _d, m in baseline if cs == old])
    if typical is None:
        return None
    known = {cs for cs, _d, _m in baseline}
    per_cs: dict[str, set[str]] = {}
    for cs, d, m in gap:
        if cs in known or _slot_distance(m, typical) > CONTINUITY_SLOT_MIN:
            continue
        per_cs.setdefault(cs, set()).add(d)
    winners = [cs for cs, days in per_cs.items() if days >= set(gap_days)]
    if len(winners) != 1:
        return None
    return {"new_callsign": winners[0], "typical_min": typical, "days": gap_days}


def _bellwether_entries_for(public_carrier: str, public_num: str, origin: str | None,
                            destination: str | None) -> list[dict]:
    from shared.watch_topic import is_bellwether
    out = []
    for e in db.get_watchlist_entries(entry_type="flight"):
        if db.split_public_number(e.get("identifier")) != (public_carrier, public_num):
            continue
        if fi.airports_agree(e.get("origin"), origin) is False:
            continue
        if fi.airports_agree(e.get("destination"), destination) is False:
            continue
        if is_bellwether(e):
            out.append(e)
    return out


def relink_by_route_continuity(today: date | None = None) -> dict:
    """Daily pass over every current atc_callsign link (continuity_decision).
    Returns counts; never raises past one link's failure."""
    today = today or datetime.now(timezone.utc).date()
    start = datetime.combine(today - timedelta(days=CONTINUITY_BASELINE_DAYS + CONTINUITY_GAP_DAYS),
                             datetime.min.time(), tzinfo=timezone.utc).timestamp()
    end = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).timestamp()
    stats = {"links": 0, "relinked": 0, "pushed": 0, "errors": 0}
    for link in db.get_atc_callsign_links():
        stats["links"] += 1
        try:
            obs = route_observations(link["operating_carrier"], link.get("origin"),
                                     link.get("destination"), start - 86400, end)
            dec = continuity_decision(link, obs, today)
            if not dec:
                continue
            new_cs = dec["new_callsign"]
            pub_ident = f"{link['marketing_carrier']}{link['marketing_flight_num']}"
            old_cs = f"{link['operating_carrier']}{link['operating_flight_num']}"
            if _ladd_blocked([pub_ident, new_cs]):
                continue
            if new_cs == pub_ident:
                # the carrier went back to filing the public number itself: retire only
                db.retire_atc_callsign_link(link["id"])
                res = {"action": "retired"}
            else:
                res = db.upsert_atc_callsign_link(pub_ident, new_cs, link.get("origin"),
                                                  link.get("destination"), "route_continuity",
                                                  season=iata_season(today))
            if not res:
                continue
            fi.clear_alias_cache()
            stats["relinked"] += 1
            log.info("lookaside: %s %s-%s relinked %s -> %s (route continuity)", pub_ident,
                     link.get("origin"), link.get("destination"), old_cs, new_cs)
            stats["pushed"] += _push_relink(link, old_cs, new_cs, dec)
        except Exception as e:  # noqa: BLE001
            stats["errors"] += 1
            log.warning("lookaside: continuity check for link %s failed: %s", link.get("id"), e)
    return stats


def _push_relink(link: dict, old_cs: str, new_cs: str, dec: dict) -> int:
    from shared.watchlist import watchlist_event_hit
    typical = f"{dec['typical_min'] // 60:02d}:{dec['typical_min'] % 60:02d}Z"
    n = 0
    for e in _bellwether_entries_for(link["marketing_carrier"], link["marketing_flight_num"],
                                     link.get("origin"), link.get("destination")):
        watchlist_event_hit(
            e["id"],
            f"ATC callsign relinked {old_cs} -> {new_cs} ({link.get('origin')}-{link.get('destination')}; "
            f"{old_cs} absent {dec['days'][-1]}..{dec['days'][0]}, {new_cs} in the {typical} +/-"
            f"{CONTINUITY_SLOT_MIN} min slot both days; SWIM route continuity, lower confidence)",
            {"watchlist_trigger": "atc_callsign_relink", "old_callsign": old_cs,
             "new_callsign": new_cs, "source": "route_continuity"},
            priority=3)
        n += 1
    return n
