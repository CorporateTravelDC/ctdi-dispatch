"""FDPS pre-departure plan guard (2026-11-18, operator).

Case: UA4006 = ASH4006 KSAV-KIAD on 2026-11-18. The watch was asked for
before departure. FDPS held only the PREVIOUS day's row (the occurrence gate,
flight_instance.fdps_plan_occurrence_rejection, correctly refused to bind it),
and the first FDPS message for the 11-18 leg reached flight_events only after
the aircraft was airborne (TFMS airline OFF 18:51Z; first FDPS row visible
between 18:34Z and 18:58Z). fdps_route_versions shows this flight's filed plan
(FH with a route) used to arrive 5-6 h ahead (12:31Z / 13:26Z, September to
2026-10-31); since 2026-11-01 none has been stored except one, 6 min before
OFF, on 11-15. Nothing told anyone that the plan was missing until the flight
was already in the air.

This module decides, per watch and tick, whether that silence is news:

  plan_missing_predeparture   no same-day FDPS plan, and the scheduled
                              departure (watch, else TFMS original/airline OUT
                              for the same day) is within LEAD_MIN minutes or
                              already past, with no sign of OFF yet
  airborne_without_plan       no same-day FDPS plan, and TFMS reports OFF for
                              the same day or the watch's own phase is off/on/in

It never binds an identity and never treats another day's plan as this one;
it only names the gap. Returns None when there is nothing to say (a same-day
plan exists, or the departure is still far off / unknown).
"""
from __future__ import annotations

from common import flight_instance as fi

LEAD_MIN = 45.0                        # warn this long before scheduled departure
AIRBORNE_PHASES = ("off", "on", "in")

# 2026-11-18 (fleet-wide check): the learned codeshare_map is sparse -- it had no
# UAL->ASH row at all, so a watch entered as UAL4006 would only ever look at
# UAL4006 and miss ASH4006's plan and TFMS times. The partners each marketing
# carrier's regionals fly for, from scripts/seed-dc-codeshare.py
# (EXCLUSIVE_REGIONAL_MARKETING inverted + AMBIGUOUS_REGIONALS; a test holds the
# two in step). US regional rule: marketing number == operating number. Jazz
# (ACA, AC 8000+n) is left out: its numbers differ, and the seed handles it.
REGIONAL_PARTNERS: dict[str, tuple[str, ...]] = {
    "UAL": ("UCA", "RPA", "SKW", "ASH", "GJS", "AWI"),
    "AAL": ("JIA", "ENY", "PDT", "RPA", "SKW", "ASH", "AWI"),
    "DAL": ("EDV", "RPA", "SKW", "GJS"),
    "ASA": ("QXE", "SKW"),
}
# operating -> the marketing carriers it can fly for (the inverse)
PARTNER_MARKETING: dict[str, tuple[str, ...]] = {}
for _mk, _ops in REGIONAL_PARTNERS.items():
    for _op in _ops:
        PARTNER_MARKETING[_op] = PARTNER_MARKETING.get(_op, ()) + (_mk,)

LINKED = ("identifier", "codeshare", "atc_callsign", "codeshare_reverse")   # map-backed
FALLBACK = ("partner_fallback", "partner_fallback_marketing")               # route-confirmed only


def candidate_identities(identifier: str | None, origin: str | None = None,
                         destination: str | None = None) -> list[tuple[str, str]]:
    """(callsign, relation) for every identity the guard checks, in order:
      identifier / codeshare / atc_callsign  fi.operating_identities_detail (marketing -> operating)
      codeshare_reverse                      codeshare_map read the other way (operating -> marketing),
                                             route-filtered the same way
      partner_fallback                       a marketing carrier's regional partners, same number
      partner_fallback_marketing             an operating regional's marketing carriers, same number
    Fallback relations are only candidates: gather() keeps one only when a same-day
    TFMS row or same-day FDPS plan matches the watch's origin AND destination."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(cs: str, rel: str) -> None:
        cs = cs.upper()
        if cs not in seen:
            seen.add(cs)
            out.append((cs, rel))

    detail = fi.operating_identities_detail(identifier, origin, destination)
    if not detail:
        return out
    for c, n, rel in detail:
        add(f"{c}{n}", rel)
    for c, n, _rel in list(detail):
        try:
            from common import db
            rows = db.get_codeshare_mapping_by_operating(c, n)
        except Exception:
            rows = []
        for r in rows:
            mk = (r.get("marketing_carrier") or "").upper()
            if not mk:
                continue
            if fi.airports_agree(r.get("origin"), origin) is False or \
                    fi.airports_agree(r.get("destination"), destination) is False:
                continue
            mn = (r.get("marketing_flight_num") or n).upper()
            add(f"{mk}{mn}", "codeshare_reverse")
    carrier, num = detail[0][0], detail[0][1]
    for op in REGIONAL_PARTNERS.get(carrier, ()):
        add(f"{op}{num}", "partner_fallback")
    for mk in PARTNER_MARKETING.get(carrier, ()):
        add(f"{mk}{num}", "partner_fallback_marketing")
    return out


def _route_matches(row: dict | None, entry: dict) -> bool:
    """Both airports positively agree (unknown is not a match for a fallback)."""
    return bool(row) and fi.airports_agree(row.get("origin"), entry.get("origin")) is True and \
        fi.airports_agree(row.get("destination"), entry.get("destination")) is True


def _tfms_same_day(row: dict | None, entry: dict, now: float) -> bool:
    if not row:
        return False
    day0 = fi.watch_day_start(entry, now)
    for k in ("original_departure", "airline_out_time", "airline_off_time"):
        t = fi.to_epoch(row.get(k))
        if t is not None:
            return _same_day(t, day0)
    return False


def gather(entry: dict, ident: str | None, now: float, get_plan, get_tfms) -> dict:
    """Plan and TFMS row for the watch across ALL its identities, choosing the
    same-occurrence one instead of the first identity with any row (a stale
    marketing row must never hide the operating callsign's same-day plan).

    get_plan(cs) / get_tfms(cs): the db lookups (injected for tests).
    Returns {"plan", "tfms", "checked": [cs...], "linked": [(cs, relation)...]}
    where linked lists the identities whose rows were used. Never binds or
    writes anything: a fallback candidate only feeds this check."""
    plans: list[tuple[str, str, dict]] = []
    tfmss: list[tuple[str, str, dict]] = []
    checked: list[str] = []
    for cs, rel in candidate_identities(ident or entry.get("identifier"),
                                        entry.get("origin"), entry.get("destination")):
        checked.append(cs)
        p, t = get_plan(cs), get_tfms(cs)
        if rel in FALLBACK:
            p_ok = bool(p) and _route_matches(p, entry) and \
                fi.fdps_plan_occurrence_rejection(entry, p, now_epoch=now) is None
            t_ok = _route_matches(t, entry) and _tfms_same_day(t, entry, now)
            if not (p_ok or t_ok):
                continue                      # an unconfirmed guess: ignored entirely
            p = p if p_ok else None
            t = t if t_ok else None
        if p:
            plans.append((cs, rel, p))
        if t:
            tfmss.append((cs, rel, t))

    def pick_plan():
        for cs, rel, p in plans:
            if fi.fdps_plan_occurrence_rejection(entry, p, now_epoch=now) is None:
                return cs, rel, p
        return plans[0] if plans else None

    def pick_tfms():
        for cs, rel, t in tfmss:
            if _tfms_same_day(t, entry, now) and fi.airports_agree(t.get("origin"), entry.get("origin")) is not False \
                    and fi.airports_agree(t.get("destination"), entry.get("destination")) is not False:
                return cs, rel, t
        return tfmss[0] if tfmss else None

    pp, tt = pick_plan(), pick_tfms()
    linked = []
    for x in (pp, tt):
        if x and (x[0], x[1]) not in linked:
            linked.append((x[0], x[1]))
    return {"plan": pp[2] if pp else None, "tfms": tt[2] if tt else None,
            "checked": checked, "linked": linked}


def _fmt(ep: float | None) -> str:
    """UTC with ET beside it (glossary rule: never ET alone)."""
    if ep is None:
        return "?"
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    d = datetime.fromtimestamp(ep, tz=timezone.utc)
    return f"{d:%H:%MZ} ({d.astimezone(ZoneInfo('America/New_York')):%H:%M} ET)"


def _same_day(epoch: float | None, day0: float) -> bool:
    return epoch is not None and day0 <= epoch < day0 + 36 * 3600


def assess(entry: dict, plan: dict | None, tfms: dict | None, now: float,
           lead_min: float = LEAD_MIN) -> dict | None:
    """The guard's verdict for one watch at `now`, or None.

    entry: watchlist row (id, identifier, origin, destination,
           scheduled_departure, oooi_phase).
    plan:  newest FDPS plan for the operating callsign (db.get_flight_plan_by_callsign
           shape), or None.
    tfms:  newest TFMS OOOI row for the operating callsign
           (db.get_recent_flight_ooooi shape), or None.
    """
    if plan and fi.fdps_plan_occurrence_rejection(entry, plan, now_epoch=now) is None:
        return None                     # this leg's own FDPS plan is on file
    day0 = fi.watch_day_start(entry, now)
    t_sched = fi.to_epoch(entry.get("scheduled_departure"))
    t_off = None
    sched_from_tfms = False
    if tfms:
        cand = fi.to_epoch(tfms.get("original_departure")) or fi.to_epoch(tfms.get("airline_out_time"))
        if t_sched is None and _same_day(cand, day0):
            t_sched = cand
            sched_from_tfms = True
        off = fi.to_epoch(tfms.get("airline_off_time"))
        if _same_day(off, day0):
            t_off = off
    phase = (entry.get("oooi_phase") or "").lower()
    stale = None
    if plan:
        stale = fi.fdps_plan_occurrence_rejection(entry, plan, now_epoch=now)
    base = {"callsign": entry.get("identifier"), "origin": entry.get("origin"),
            "destination": entry.get("destination"), "watch_id": entry.get("id"),
            "scheduled_departure": t_sched, "newest_plan_rejected": stale}
    if phase in AIRBORNE_PHASES or (t_off is not None and t_off <= now):
        return {**base, "state": "airborne_without_plan", "off": t_off,
                "summary": (f"{entry.get('identifier')} {entry.get('origin')}-{entry.get('destination')}: "
                            f"airborne with no FDPS plan for this leg on file"
                            + (f" (OFF {_fmt(t_off)})" if t_off else ""))}
    if t_sched is not None and now >= t_sched - lead_min * 60:
        return {**base, "state": "plan_missing_predeparture",
                "summary": (f"{entry.get('identifier')} {entry.get('origin')}-{entry.get('destination')}: "
                            + (f"FDPS plan not yet visible (TFMS has the schedule: departure {_fmt(t_sched)}); "
                               if sched_from_tfms or tfms else
                               f"no FDPS plan for this leg yet, departure {_fmt(t_sched)}; ")
                            + "tail confirmation is waiting on it")}
    return None
