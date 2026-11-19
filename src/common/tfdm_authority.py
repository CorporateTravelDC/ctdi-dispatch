"""TFDM pre-departure source authority (2026-11-19, operator).

Operator: "add TFDM into the source authority and allow it to precede SFDPS, as it is actually a
pre-departure clearance and would come out, presumably in most cases, prior to the flight plan
itself. It could also alert on a flight plan that is still awaiting clearances, as well as EDCTs or
CDRs, prior to a flight plan being officially deployed and filed."

Case behind it: UA4006 = ASH4006 KSAV-KIAD, 2026-11-18. The watch waited for a tail all
afternoon because the leg's first FDPS message reached us only after takeoff; the 11-18 fleet-wide
check found 80-94 % of legs on every carrier have no stored pre-departure FDPS route version.

What this module does, for WATCHED legs only, from one TTP Flight Data record
(ingest.parsers.tfdm_parser):

1. Identity, before departure only. A watch still waiting for its tail binds to the record's
   aircraft/@registration (ATD-2 TTP FIXM table: AircraftRegistrationMark) when ALL hold:
     - same occurrence: the record's off-block time (TOBT, else scheduled, earliest, initial)
       falls on the watch's own America/New_York date, and origin and destination agree;
     - not airborne: no actual takeoff on the record, watch phase not off/on/in;
     - registry cross-check: the FAA registry gives a Mode S hex for the registration, and the
       OpenSky registry, if it knows the registration, gives the same hex. A disagreement is
       "conflicting": nothing binds and one push says so.
   The TTP documents carry no ICAO 24-bit address, so the hex always comes from the registry.
   A watch that already has a tail is never repointed here; after OFF the FDPS/TBFM/TFMS order is
   unchanged (flight_instance.AUTHORITATIVE_TAIL_SOURCES lists "tfdm" for this path only).
   LADD: the confirmation push withholds the identifier exactly as shared.pending_identity does.

2. Alerts, through the push parity gate (dedupe on stable fields, never timestamps):
     - fdps_plan_missing_predeparture, state plan_missing_predeparture: the SAME kind and state the
       poller's guard (common.fdps_predeparture) uses, so whichever source notices first sends the
       one push and the other is suppressed. TFDM adds its off-block time to the text.
     - tfdm_awaiting_clearance: no clearance on the record by CLEARANCE_LEAD_MIN before off-block.
       [VERIFY] the clearance field's path is not in the ATD-2 FIXM table, so this alert stays off
       until the feed has been seen to carry a clearance element on any flight (self-calibrating;
       otherwise every flight would look uncleared).
     - tfdm_tmi: the record's TMI identifiers (departureDelay/@tmiIdentifier), once per distinct set.
     - tfdm_apreq_release: an APREQ release time (approvalRequestReleaseTime), once per value.
   EDCT and CDR are not in the TTP documents: EDCT keeps coming from TFMS (kind tfms_edct), and no
   CDR alert exists until a source documents one.
"""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("common.tfdm_authority")

LEAD_MIN = float(os.environ.get("TFDM_PLAN_MISSING_LEAD_MIN", "45"))      # same as fdps_predeparture
CLEARANCE_LEAD_MIN = float(os.environ.get("TFDM_CLEARANCE_LEAD_MIN", "30"))
AIRBORNE_PHASES = ("off", "on", "in")
_WATCH_TTL_S = 60.0

_lock = threading.Lock()
_watch_cache: dict = {"at": 0.0, "by_callsign": {}}
_clearance_field_seen = False


def _now(now: float | None) -> float:
    return time.time() if now is None else now


def _fmt(ep: float | None) -> str:
    if ep is None:
        return "?"
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    z = datetime.fromtimestamp(ep, timezone.utc)
    et = z.astimezone(ZoneInfo("America/New_York"))
    return f"{z:%H:%M}Z ({et:%H:%M} ET)"


def off_block_epoch(rec: dict) -> float | None:
    """The record's best pre-departure off-block time: TOBT, then scheduled, earliest, initial."""
    from common.flight_instance import to_epoch
    for k in ("tobt", "sched_off_block", "earliest_off_block", "initial_off_block"):
        t = to_epoch(rec.get(k))
        if t is not None:
            return t
    return None


def same_occurrence(entry: dict, rec: dict, now: float | None = None) -> str | None:
    """Why this TFDM record is NOT the watch's dated leg, or None when it is. Fail closed: no
    off-block time at all, or an airport that disagrees, is a rejection."""
    from common import flight_instance as fi
    t = off_block_epoch(rec)
    if t is None:
        return "record_has_no_off_block_time"
    day0, day1 = fi.watch_day_start(entry, now), fi.watch_day_end(entry, now)
    if not (day0 <= t < day1):
        return "other_day_occurrence"
    if fi.airports_agree(entry.get("origin"), rec.get("departure_airport")) is False:
        return "origin_disagrees"
    if fi.airports_agree(entry.get("destination"), rec.get("destination_airport")) is False:
        return "destination_disagrees"
    return None


def airborne(entry: dict, rec: dict) -> bool:
    return bool(rec.get("actual_takeoff")) or (entry.get("oooi_phase") or "").lower() in AIRBORNE_PHASES


def registry_hex(registration: str) -> tuple[str | None, str]:
    """(hex, verdict) from the local registries. verdict: 'agree' (FAA hex, OpenSky same or
    unknown), 'conflicting' (both know it and differ), 'unknown' (FAA has no hex)."""
    from common import db
    faa = osk = None
    try:
        row = db.faa_lookup_by_n_number(registration)
        faa = (row or {}).get("mode_s_hex")
    except Exception as e:
        log.debug("tfdm: FAA registry %s: %s", registration, type(e).__name__)
    try:
        row = db.opensky_lookup_by_registration(registration)
        osk = (row or {}).get("icao24")
    except Exception as e:
        log.debug("tfdm: OpenSky registry %s: %s", registration, type(e).__name__)
    faa = faa.lower().strip() if faa else None
    osk = osk.lower().strip() if osk else None
    if not faa:
        return None, "unknown"
    if osk and osk != faa:
        return None, "conflicting"
    return faa, "agree"


def _watched(now: float) -> dict[str, list[dict]]:
    """callsign -> flight watch entries answering to it (identifier, codeshare both ways and the
    regional partners: common.fdps_predeparture.candidate_identities). Cached 60 s, so a feed that
    carries every aerodrome costs one dict lookup per flight."""
    with _lock:
        if now - _watch_cache["at"] < _WATCH_TTL_S:
            return _watch_cache["by_callsign"]
    from common import db
    from common.fdps_predeparture import candidate_identities
    by: dict[str, list[dict]] = {}
    try:
        for e in db.get_watchlist_entries():
            if (e.get("entry_type") or "flight") != "flight" or not e.get("identifier"):
                continue
            for cs, _rel in candidate_identities(e["identifier"], e.get("origin"), e.get("destination")):
                by.setdefault(cs.upper(), []).append(e)
    except Exception as ex:
        log.debug("tfdm: watch list: %s", type(ex).__name__)
    with _lock:
        _watch_cache.update(at=now, by_callsign=by)
    return by


def _hit(entry: dict, summary: str, detail: dict, priority: int) -> None:
    from shared.watchlist import watchlist_event_hit
    watchlist_event_hit(entry["id"], summary, {**detail, "source": "tfdm"}, priority=priority)


def bind_identity(entry: dict, rec: dict, now: float | None = None) -> str:
    """Bind a pending watch's tail from TFDM. Returns what happened (for logs and tests)."""
    from common import db
    if entry.get("hex_id") or entry.get("registration"):
        return "already_identified"
    reg = rec.get("registration")
    if not reg:
        return "no_registration"
    if airborne(entry, rec):
        return "airborne"
    why = same_occurrence(entry, rec, now)
    if why:
        return why
    hx, verdict = registry_hex(reg)
    if verdict == "conflicting":
        _hit(entry, f"{entry.get('identifier')}: TFDM tail does not match the registries (FAA vs OpenSky); "
                    f"not bound, still awaiting tail",
             {"watchlist_trigger": "identity_mismatch_reg", "state": "tfdm_registry_conflict"}, 4)
        return "conflicting"
    if not hx:
        return "registry_unknown"
    db.set_watchlist_identity(entry["id"], hex_id=hx, registration=reg, source="tfdm")
    entry["hex_id"], entry["registration"] = hx, reg     # the 60 s watch cache must not re-bind
    from shared.pending_identity import confirmed_summary
    summary, withheld = confirmed_summary(hx, reg)
    detail = {"watchlist_trigger": "identity_resolved", "lookup": "tfdm", "withheld": withheld,
              "off_block": off_block_epoch(rec)}
    if not withheld:
        detail.update({"hex_id": hx, "registration": reg,
                       "tracking_url": f"https://globe.airplanes.live/?icao={hx}"})
    _hit(entry, summary + " (TFDM, before departure)", detail, 3)
    log.info("tfdm: %s bound to its tail before departure (registry agree)", entry.get("id"))
    return "bound"


def alerts(entry: dict, rec: dict, now: float | None = None, has_fdps_plan=None) -> list[str]:
    """Send the pre-departure alerts for one watched, same-occurrence record. Returns their kinds."""
    global _clearance_field_seen
    now = _now(now)
    sent: list[str] = []
    if same_occurrence(entry, rec, now) or airborne(entry, rec):
        return sent
    t = off_block_epoch(rec)
    leg = f"{entry.get('identifier')} {entry.get('origin')}-{entry.get('destination')}"
    if has_fdps_plan is None:
        from shared.pending_identity import _has_occurrence_plan
        has_fdps_plan = _has_occurrence_plan
    try:
        plan_on_file = bool(has_fdps_plan(entry))
    except Exception:
        plan_on_file = True                      # unknown -> say nothing rather than a false alarm
    if not plan_on_file and t is not None and now >= t - LEAD_MIN * 60:
        _hit(entry, f"{leg}: no FDPS plan for this leg yet; TFDM has the flight (off-block {_fmt(t)}); "
                    f"tail confirmation is waiting on it",
             {"watchlist_trigger": "fdps_plan_missing_predeparture", "state": "plan_missing_predeparture",
              "scheduled_departure": t}, 4)
        sent.append("fdps_plan_missing_predeparture")
    if rec.get("clearance_delivery"):
        _clearance_field_seen = True
    elif _clearance_field_seen and t is not None and now >= t - CLEARANCE_LEAD_MIN * 60:
        _hit(entry, f"{leg}: awaiting clearance (TFDM shows none; off-block {_fmt(t)})",
             {"watchlist_trigger": "tfdm_awaiting_clearance", "state": "awaiting_clearance"}, 4)
        sent.append("tfdm_awaiting_clearance")
    if rec.get("tmi_ids"):
        ids = ",".join(rec["tmi_ids"])
        _hit(entry, f"{leg}: traffic management initiative on this departure ({ids})",
             {"watchlist_trigger": "tfdm_tmi", "tmi": ids}, 4)
        sent.append("tfdm_tmi")
    if rec.get("apreq_release"):
        from common.flight_instance import to_epoch
        _hit(entry, f"{leg}: APREQ release time {_fmt(to_epoch(rec['apreq_release']))}",
             {"watchlist_trigger": "tfdm_apreq_release", "release": rec["apreq_release"]}, 4)
        sent.append("tfdm_apreq_release")
    return sent


def process(rec: dict, now: float | None = None) -> bool:
    """Entry point from the TFDM handler. True when a watch was bound. Never raises."""
    try:
        now = _now(now)
        cs = (rec.get("callsign") or "").upper()
        if not cs:
            return False
        bound = False
        for entry in _watched(now).get(cs, ()):
            if same_occurrence(entry, rec, now):
                continue
            if bind_identity(entry, rec, now) == "bound":
                bound = True
            alerts(entry, rec, now)
        return bound
    except Exception as e:
        log.debug("tfdm: authority for %s: %s", rec.get("callsign"), type(e).__name__)
        return False
