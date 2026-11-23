"""Pending-identity flight watches (2026-11-18, operator).

A flight watch added before the day's FAA plan exists has no verified airframe:
POST /api/v1/watchlist/flights stores it hex-pending (hex_id and registration
empty -- never filled from FIDS, an aggregator or another day's plan; see
flight_instance.fdps_plan_occurrence_rejection). Before this module such a
watch was silent until the aircraft showed up. Operator, UA4006 SAV-IAD on
2026-11-18: "can we at least send a pre-departure NTFY alert so that it's
indicated that it's in the queue for a tail number confirmation (for both a
visibility capability and ensuring that it gets essentially a milestone check,
even pre-departure)".

    identity_state(entry)  -> "pending_identity" | "bound"
    queued_push(entry, plan_filed)   one p3 at creation (identity_queued)
    sweep(entries, now)              per poller tick, pending watches only:
        - scheduled departure known and now >= sched - 60 min -> one p4
          identity_still_pending ("still awaiting tail")
        - the watch's ET date has ended with no plan for this occurrence ->
          one identity_expired_no_plan ("no plan filed; watch expired"), and
          the watch is removed
    confirmed_summary(hex, reg)      the "IDENTITY CONFIRMED" text, LADD-aware

Binding itself stays where it was (shared.watchlist.resolve_flight_identity,
on first same-occurrence contact). Every push goes through
shared.watchlist.watchlist_event_hit and so through common.push_policy; the
dedupe slot is the entry id (callsign + ET date) and the state, never a
timestamp or wording. A pending watch is never presented as confirmed.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

PENDING = "pending_identity"
BOUND = "bound"
STILL_PENDING_LEAD_S = 60 * 60          # "still awaiting tail" this long before scheduled departure


def identity_state(entry: dict) -> str | None:
    """pending_identity for a flight watch with no hex and no registration, bound when it has
    either; None for other entry types."""
    if (entry.get("entry_type") or "flight") != "flight":
        return None
    if (entry.get("hex_id") or "").strip() or (entry.get("registration") or "").strip():
        return BOUND
    return PENDING


def _occurrence(entry: dict) -> str:
    from common.flight_instance import _WATCH_ID_DATE_RE, watch_id_date
    m = _WATCH_ID_DATE_RE.search(str(entry.get("id") or ""))
    return m.group(1) if m else watch_id_date(entry.get("scheduled_departure"))


def _date_label(occ: str) -> str:
    return f"{occ[0:4]}-{occ[4:6]}-{occ[6:8]}" if len(occ) == 8 else occ


def _utc_et(epoch: float) -> str:
    from zoneinfo import ZoneInfo
    u = datetime.fromtimestamp(epoch, timezone.utc)
    et = u.astimezone(ZoneInfo("America/New_York"))
    return f"{u:%H:%MZ} ({et:%H:%M} ET)"


def _route(entry: dict) -> str:
    o, d = (entry.get("origin") or "").upper(), (entry.get("destination") or "").upper()
    return f"{o}-{d}" if o or d else ""


def _proposed_text(proposed: tuple[float, str] | None) -> str:
    return f"; proposed departure {_utc_et(proposed[0])}, {proposed[1]}" if proposed else ""


def queued_summary(entry: dict, plan_filed: bool = False,
                   proposed: tuple[float, str] | None = None) -> str:
    why = ("plan filed, no aircraft assigned yet" if plan_filed
           else "no plan for this leg yet")
    return (f"QUEUED {entry.get('identifier')} {_route(entry)} {_date_label(_occurrence(entry))}: "
            f"awaiting tail confirmation ({why}{_proposed_text(proposed)})")


def queued_push(entry: dict, plan_filed: bool = False,
                proposed: tuple[float, str] | None = None) -> None:
    """One p3 'QUEUED ... awaiting tail confirmation' for a just-added pending watch. 2026-11-21: with
    the leg's proposed departure when one is known (FDPS proposed runway time, else TFMS schedule)."""
    from shared.watchlist import watchlist_event_hit
    watchlist_event_hit(
        entry["id"], queued_summary(entry, plan_filed, proposed),
        {"watchlist_trigger": "identity_queued", "state": PENDING,
         "occurrence": _occurrence(entry), "plan_filed": bool(plan_filed),
         "proposed_departure": proposed[0] if proposed else None},
        priority=3,
    )


def is_withheld_identity(hex_id: str | None, registration: str | None) -> bool:
    """True if either identifier is LADD-listed. Fails closed: a lookup error counts as listed."""
    from common import db
    try:
        if hex_id and db.faa_ladd_hex_batch([hex_id]):
            return True
        if registration and db.faa_is_ladd(registration):
            return True
        return False
    except Exception as e:  # noqa: BLE001
        log.warning("pending_identity: LADD check failed (%s) -- identifier withheld", e)
        return True


def confirmed_summary(hex_id: str | None, registration: str | None) -> tuple[str, bool]:
    """('IDENTITY CONFIRMED <tail>/<hex>', withheld). A LADD-listed (or unverifiable) airframe
    gets no identifier at all."""
    if is_withheld_identity(hex_id, registration):
        return "IDENTITY CONFIRMED (withheld aircraft: identifier not shown)", True
    return f"IDENTITY CONFIRMED {registration or '?'}/{hex_id or '?'}", False


def to_epoch_safe(value) -> float | None:
    from common.flight_instance import to_epoch
    try:
        return to_epoch(value)
    except Exception:  # noqa: BLE001
        return None


def _sched_epoch(entry: dict) -> float | None:
    from common.flight_instance import to_epoch
    return to_epoch(entry.get("scheduled_departure"))


def _day_end(entry: dict, now: float) -> float:
    """00:00 America/New_York the next calendar day (23/25 h on DST days). 2026-11-19 overnight grace:
    a watch queued 20:00-24:00 ET on its own date runs to 02:00 ET the next day instead."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from common.flight_instance import (OVERNIGHT_GRACE_END_HOUR, OVERNIGHT_GRACE_START_HOUR, to_epoch,
                                        watch_day_end, watch_day_start)
    end = watch_day_end(entry, now)
    added = to_epoch(entry.get("added_at"))
    if added is not None:
        tz = ZoneInfo("America/New_York")
        start = watch_day_start(entry, now)
        a = datetime.fromtimestamp(added, tz)
        if start <= added < end and a.hour >= OVERNIGHT_GRACE_START_HOUR:
            nd = datetime.fromtimestamp(end, tz).date()
            end = datetime(nd.year, nd.month, nd.day, OVERNIGHT_GRACE_END_HOUR, tzinfo=tz).timestamp()
    return end


def plan_is_this_leg(entry: dict, plan: dict | None) -> bool:
    """The plan is the watch's own dated leg: same occurrence AND neither airport contradicts the
    watch's. 2026-11-21 (UA2072 KDJT-KIAD): the add route counted the same day's ORD-KDJT inbound plan
    as "plan filed" for the DJT-IAD watch, because only the date was checked."""
    if not plan:
        return False
    from common.flight_instance import airports_agree, fdps_plan_occurrence_rejection
    return (airports_agree(entry.get("origin"), plan.get("origin")) is not False
            and airports_agree(entry.get("destination"), plan.get("destination")) is not False
            and fdps_plan_occurrence_rejection(entry, plan) is None)


def _occurrence_plan(entry: dict) -> dict | None:
    """This occurrence's FDPS plan via the same lookups the add route uses, or None. Raises on a
    failed lookup (callers decide what unknown means)."""
    from common import db
    ident = (entry.get("identifier") or "").upper()
    p = db.get_flight_plan_by_callsign(ident)
    if not p:
        op = db.resolve_operating_callsign(ident)
        if op:
            p = db.get_flight_plan_by_callsign(op)
    if not p:
        p = db.get_flight_plan_by_flight_num(ident, origin=(entry.get("origin") or None))
    return p if plan_is_this_leg(entry, p) else None


def _has_occurrence_plan(entry: dict) -> bool:
    """True if FDPS holds a plan for THIS occurrence and route (any status). Another day's plan, or
    another leg of the same number, does not count."""
    try:
        return _occurrence_plan(entry) is not None
    except Exception as e:  # noqa: BLE001
        log.debug("pending_identity %s: plan lookup failed: %s", entry.get("identifier"), e)
        return True     # unknown -> never expire on a failed lookup


def proposed_departure(entry: dict, plan: dict | None = None) -> tuple[float, str] | None:
    """(epoch, source) of the queued leg's proposed departure, or None. 2026-11-21 (operator): a watch
    whose tail is still queued shows when the leg is proposed to leave, once a plan is listed.
    Order: this leg's FDPS plan (its proposed runway departure), else TFMS's scheduled departure for
    the same route on the watch's own ET date. Never raises."""
    from common.flight_instance import airports_agree, to_epoch, watch_day_end, watch_day_start
    try:
        p = plan if plan_is_this_leg(entry, plan) else _occurrence_plan(entry)
        t = to_epoch((p or {}).get("departure_time"))
        if t is not None:
            return t, "FDPS"
        from common import db
        r = db.get_recent_flight_ooooi((entry.get("identifier") or "").upper())
        if r and airports_agree(entry.get("origin"), r.get("origin")) is not False \
                and airports_agree(entry.get("destination"), r.get("destination")) is not False:
            t = to_epoch(r.get("original_departure"))
            if t is not None and watch_day_start(entry) <= t < watch_day_end(entry):
                return t, "TFMS schedule"
    except Exception as e:  # noqa: BLE001
        log.debug("pending_identity %s: proposed departure: %s", entry.get("id"), e)
    return None


def _plan_first_seen(plan: dict) -> float | None:
    from common.flight_instance import to_epoch
    return to_epoch(plan.get("first_seen_at")) or to_epoch(plan.get("updated_at"))


def sweep(entries: list[dict], now: float | None = None) -> dict:
    """Pre-departure checks for pending-identity flight watches. Returns counts."""
    from common import db
    from shared.watchlist import watchlist_event_hit
    now = time.time() if now is None else float(now)
    out = {"pending": 0, "still_pending": 0, "expired": 0, "plan_filed": 0}
    for e in entries:
        if identity_state(e) != PENDING:
            continue
        out["pending"] += 1
        occ = _occurrence(e)
        ident = e.get("identifier")
        try:
            if now >= _day_end(e, now):
                if not _has_occurrence_plan(e):
                    watchlist_event_hit(
                        e["id"],
                        f"{ident} {_route(e)} {_date_label(occ)}: no plan filed; watch expired "
                        f"(no tail was ever confirmed)",
                        {"watchlist_trigger": "identity_expired_no_plan", "state": "expired_no_plan",
                         "occurrence": occ},
                        priority=3,
                    )
                    db.delete_watchlist_entry(e["id"])
                    out["expired"] += 1
                continue
            # 2026-11-21 (operator): the plan for this leg was listed AFTER the watch was queued ->
            # one p3 with its proposed departure, while the tail is still queued.
            try:
                plan = _occurrence_plan(e)
            except Exception:  # noqa: BLE001
                plan = None
            proposed = proposed_departure(e, plan)
            seen, added = (_plan_first_seen(plan) if plan else None), to_epoch_safe(e.get("added_at"))
            if plan and seen is not None and added is not None and seen > added:
                watchlist_event_hit(
                    e["id"],
                    f"{ident} {_route(e)} {_date_label(occ)}: plan filed, still awaiting tail"
                    f"{_proposed_text(proposed)}",
                    {"watchlist_trigger": "identity_plan_filed", "state": "plan_filed",
                     "occurrence": occ, "proposed_departure": proposed[0] if proposed else None},
                    priority=3,
                )
                out["plan_filed"] = out.get("plan_filed", 0) + 1
            sched = _sched_epoch(e)
            label = "scheduled departure"
            if sched is None and proposed is not None:
                sched, label = proposed[0], f"proposed departure, {proposed[1]}"
            if sched is not None and now >= sched - STILL_PENDING_LEAD_S:
                watchlist_event_hit(
                    e["id"],
                    f"{ident} {_route(e)} {_date_label(occ)}: still awaiting tail "
                    f"({label} {_utc_et(sched)})",
                    {"watchlist_trigger": "identity_still_pending", "state": "still_pending",
                     "occurrence": occ},
                    priority=4,
                )
                out["still_pending"] += 1
        except Exception as ex:  # noqa: BLE001
            log.debug("pending_identity sweep %s: %s", e.get("id"), ex)
    return out
