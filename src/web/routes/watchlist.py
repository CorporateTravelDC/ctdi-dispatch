"""
web.routes.watchlist — Permanent + transient watchlist REST API.

GET  /api/v1/watchlist              List all active entries (Tier 1: Tailscale/token)
GET  /api/v1/watchlist/history      Recent events (Tier 1: Tailscale/token)
POST /api/v1/watchlist/flights      Add transient flight entry (Admin)
POST /api/v1/watchlist/trains       Add transient train entry (Admin)
DELETE /api/v1/watchlist/{id}       Remove an entry (Admin)

VIP watchlist contents (who/what is being tracked) are not Tier 0: this
hostname has no Cloudflare Access gate, so Tier 0 here means the raw
public internet. Reads require at least Tier 1 (on Tailscale, or a
cert/shares/admin bearer token); writes remain Admin-only as before.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from auth.auth import Tier, require_admin, require_tier
from common import db
from shared.watchlist import _fire_ntfy_dual, PERMANENT_WATCHLIST_DIR

router = APIRouter(prefix="/api/v1/watchlist", tags=["watchlist"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_id(entry_type: str, identifier: str) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    ident_slug = identifier.lower().replace(" ", "-")
    return f"wl-{entry_type}-{ident_slug}-{date_str}"


def _default_auto_remove_at(scheduled_arrival: Optional[str],
                             buffer_hours: float,
                             fallback_hours: float = 24.0) -> str:
    """
    Compute a transient watchlist entry's expiry when the caller doesn't
    supply one. Without this, entries created via the normal add flow
    (which historically never set auto_remove_at) get auto_remove_at=NULL
    and the sweep's `WHERE auto_remove_at IS NOT NULL` clause can never
    match them -- functionally permanent despite being tagged transient.
    This was the root cause of UA1453/DL2962 never sweeping (2026-07-21).

    If a scheduled_arrival is known, expire buffer_hours after it (covers
    delays/diversions without leaving a dead entry indefinitely). If not
    known yet (e.g. added pre-departure), fall back to added-time +
    fallback_hours so a bad/missing arrival estimate can't make the entry
    immortal either.
    """
    from datetime import timedelta

    base = None
    if scheduled_arrival:
        try:
            base = datetime.fromisoformat(scheduled_arrival.replace("Z", "+00:00"))
        except ValueError:
            base = None

    if base is not None:
        expiry = base + timedelta(hours=buffer_hours)
    else:
        expiry = datetime.now(timezone.utc) + timedelta(hours=fallback_hours)

    return expiry.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── GET /api/v1/watchlist ─────────────────────────────────────────────────────

@router.get("")
async def list_watchlist_entries(
    tier: Tier = Depends(require_tier(Tier.T1)),
) -> JSONResponse:
    """List all active watchlist entries (permanent + transient). Tier 1+."""
    entries = db.get_watchlist_entries()
    return JSONResponse({"entries": entries, "count": len(entries)})


# ── GET /api/v1/watchlist/history ─────────────────────────────────────────────

@router.get("/history")
async def watchlist_history(
    limit: int = 50,
    tier: Tier = Depends(require_tier(Tier.T1)),
) -> JSONResponse:
    """Recent watchlist events. Tier 1+."""
    limit = min(limit, 200)
    rows = db.get_watchlist_history(limit=limit)
    return JSONResponse({"history": rows, "count": len(rows)})


# ── POST /api/v1/watchlist/flights ────────────────────────────────────────────

class FlightWatchlistRequest(BaseModel):
    identifier: str
    origin: Optional[str] = None
    destination: Optional[str] = None
    scheduled_departure: Optional[str] = None
    scheduled_arrival: Optional[str] = None
    auto_remove_at: Optional[str] = None
    notes: Optional[str] = None
    added_by: str = "api"
    # 2026-07-22: hex_id/registration were already columns on
    # watchlist_entries (schema v18) and already read by
    # poller._check_flight_airplanes_live's identity-mismatch check --
    # but this endpoint never exposed a way to *set* them on add, so
    # every transient entry ever created here had expected_hex=None.
    # Combined with `identifier` never being normalized to an ICAO
    # callsign (airplanes.live's callsign endpoint wants "AAL2773", not
    # the IATA-style "AA2773" callers were passing), this meant
    # transient flight entries could silently never resolve on
    # airplanes.live at all -- they'd fall through to the weaker
    # schedule-inference fallback while permanent entries (manually
    # pre-formatted with correct ICAO identifiers in
    # permanent_flights.json) resolved fine. Found 2026-07-22 when the
    # operator noticed transient flight-alerts were mostly missing.
    hex_id: Optional[str] = None
    registration: Optional[str] = None


@router.post("/flights", status_code=201)
async def add_flight_watchlist(
    body: FlightWatchlistRequest,
    tier: Tier = Depends(require_admin("watchlist.flight.add")),
) -> JSONResponse:
    """Add a transient flight watchlist entry. Admin required."""
    ident = body.identifier.strip().upper()
    if not ident:
        raise HTTPException(400, "identifier is required")

    entry_id = _make_id("flight", ident)
    now = _now_iso()

    # 2026-07-28: auto-resolve tail -> hex whenever a registration is given
    # and no hex_id was supplied directly. Same dual-registry logic as
    # GET /api/v1/aircraft/{identifier} (FAA authoritative for US N-numbers,
    # OpenSky fallback/cross-check) -- operator request: "for FDPS departures
    # with tail numbers let's always resolve it" so a provisional tail
    # doesn't sit un-trackable on airplanes.live just because nobody
    # remembered to look it up by hand.
    resolved_hex_id = (body.hex_id or "").strip() or None
    resolved_registration = (body.registration or "").strip() or None
    if resolved_registration and not resolved_hex_id:
        try:
            faa_rec = db.faa_lookup_by_n_number(resolved_registration)
            osky_rec = db.opensky_lookup_by_registration(resolved_registration)
            faa_hex = (faa_rec.get("mode_s_hex") or "").lower() if faa_rec else None
            osky_hex = (osky_rec.get("icao24") or "").lower() if osky_rec else None
            resolved_hex_id = faa_hex or osky_hex or None
        except Exception:
            # Registry lookup is a nice-to-have at add time, never block
            # the watchlist add itself if it errors.
            resolved_hex_id = None

    # 2026-07-27: cross-check FDPS (FAA FIXM flight-plan feed, see
    # /api/v1/flightplan/{callsign}) for this callsign. If the caller didn't
    # supply origin/destination, backfill from FDPS's filed flight plan
    # rather than leaving the entry with no route at all -- this is the
    # FAA's own filed plan, not a guess. Never overwrites an origin/
    # destination the caller actually supplied.
    #
    # 2026-07-28: get_flight_plan_by_callsign() only matches on the airline
    # code embedded in `ident` itself -- for a codeshare/regional-operated
    # flight (e.g. "UAL4044" marketed by United, actually flown as "ASH4044"
    # by Mesa/United Express) FDPS carries the record under the OPERATING
    # carrier's code, so the direct-identifier lookup silently returns None
    # even though FAA absolutely has a live flight plan for this flight.
    # Fall back to a flight_num (+ origin, if known) match across all
    # carriers when the direct lookup misses -- this is what actually
    # caught ASH4044/ASH4056 for UAL4044/UAL4056 during manual checking.
    fdps_plan = db.get_flight_plan_by_callsign(ident)
    if not fdps_plan:
        fallback_origin = (body.origin or "").strip().upper() or None
        fdps_plan = db.get_flight_plan_by_flight_num(ident, origin=fallback_origin)
    fdps_origin = (fdps_plan or {}).get("origin")
    fdps_dest = (fdps_plan or {}).get("destination")
    # "Confirmed" means FAA's own feed shows this exact flight as actively
    # filed/moving right now -- "proposed" (not yet filed), "cancelled",
    # "dropped", or no record at all are all fdps_confirmed=False, just
    # with a different reason visible in last_fdps_status.
    fdps_confirmed = bool(fdps_plan) and fdps_plan.get("status") == "active"

    # 2026-07-28 (codeshare_map Phase 1): the flight_num fallback above only
    # fires when the direct callsign lookup missed -- which means, whenever
    # it DOES find a plan, FDPS just confirmed this physical flight is filed
    # under a different carrier code than the marketing identifier the
    # caller gave us. That's a live, real-world marketing<->operating
    # confirmation -- record it instead of discarding it, so the mapping
    # accumulates on its own from ordinary dispatch use.
    if fdps_plan:
        _mkt_match = re.match(r"^([A-Za-z]+)(\d+[A-Za-z]?)$", ident.strip())
        if _mkt_match:
            _marketing_carrier, _marketing_num = _mkt_match.group(1).upper(), _mkt_match.group(2)
            _operating_carrier = (fdps_plan.get("airline") or "").upper() or None
            if _operating_carrier and _operating_carrier != _marketing_carrier:
                try:
                    db.upsert_codeshare_mapping(
                        marketing_carrier=_marketing_carrier,
                        marketing_flight_num=_marketing_num,
                        operating_carrier=_operating_carrier,
                        operating_flight_num=fdps_plan.get("flight_num"),
                        origin=fdps_origin,
                        destination=fdps_dest,
                        source="fdps_fallback_match",
                    )
                except Exception:
                    pass

    entry = {
        "id": entry_id,
        "entry_type": "flight",
        "tier": "transient",
        "identifier": ident,
        "origin": (body.origin or "").upper() or fdps_origin or None,
        "destination": (body.destination or "").upper() or fdps_dest or None,
        "route_name": None,
        "scheduled_departure": body.scheduled_departure,
        "scheduled_arrival": body.scheduled_arrival,
        "auto_remove_at": body.auto_remove_at or _default_auto_remove_at(
            body.scheduled_arrival, buffer_hours=6.0),
        "added_at": now,
        "added_by": body.added_by,
        "notes": body.notes,
        "hex_id": resolved_hex_id,
        "registration": resolved_registration,
        "last_event_at": None,
        "last_event_summary": None,
    }
    db.upsert_watchlist_entry(entry)

    # Persist the FDPS read at add-time (previously response-only, see
    # docstring on update_watchlist_fdps_confirmation for why this matters --
    # a GET on this entry a minute later used to show no trace of what FDPS
    # said when it was added).
    fdps_status_value = (fdps_plan or {}).get("status")
    db.update_watchlist_fdps_confirmation(entry_id, fdps_status_value, now)

    origin = entry["origin"] or ""
    dest = entry["destination"] or ""
    route = f"{origin}→{dest}" if origin or dest else ""
    expire_str = ""
    if body.auto_remove_at:
        try:
            exp = datetime.fromisoformat(body.auto_remove_at.replace("Z", "+00:00"))
            expire_str = f" — auto-expire {exp.strftime('%H:%M')} UTC"
        except ValueError:
            expire_str = f" — auto-expire {body.auto_remove_at}"

    # 2026-07-27: hex_id/registration have been real columns (and real
    # request fields, see FlightWatchlistRequest above) since the
    # 2026-07-22 fix, but this notification never surfaced them -- the
    # "Watching <ident>" push looked identical whether or not the caller
    # supplied a hex, so there was no quick glance confirmation of which
    # physical airframe got matched. Found 2026-07-27 when the operator
    # noticed the watchlist-add push for UAL2670 had no hex in it.
    id_tag = f" [{entry['hex_id']}]" if entry["hex_id"] else ""
    reg_tag = f" {entry['registration']}" if entry["registration"] else ""
    # 2026-07-28: plain yes/no FDPS tag on the push itself, not just buried
    # in the API response -- operator request, so it is glanceable on the
    # phone the moment the watchlist-add push lands, same as the hex tag.
    fdps_tag = " FDPS:Y" if fdps_confirmed else " FDPS:N"

    _fire_ntfy_dual(
        domain_topic="flight-alerts",
        title=f"Watching {ident}{id_tag} {route}",
        detail_body=f"Flight {ident}{id_tag}{reg_tag} {route} added to watchlist{expire_str}{fdps_tag}",
        dispatch_body=f"Watchlist: {ident}{id_tag} added (transient){fdps_tag}",
        priority=2,
    )

    response = dict(entry)
    response["fdps_confirmed"] = fdps_confirmed
    if fdps_plan:
        response["fdps_detail"] = {
            "aircraft_type": fdps_plan.get("aircraft_type"),
            "status": fdps_plan.get("status"),
            "origin_used": fdps_origin is not None and not body.origin,
            "destination_used": fdps_dest is not None and not body.destination,
        }

    # 2026-08-20: real (not proxied) on-time departure/arrival history for
    # this flight number, from actual TFMS-reported airline OOOI times vs.
    # TFMS's originalDeparture/originalArrival -- see
    # db.get_flight_ontime_history()'s docstring. Only ever non-empty for a
    # flight number that has itself been watchlisted before (TFMS OOOI
    # capture is watchlist-gated, same as the rest of tfms_parser.py) --
    # "insufficient_data" is the honest, expected state for a
    # never-before-watched flight number or one added before 2026-08-20,
    # not a bug or a sign the flight isn't operating.
    _m = re.match(r"^([A-Za-z]+)(\d+[A-Za-z]?)$", ident)
    if _m:
        try:
            response["ontime_history_14d"] = db.get_flight_ontime_history(
                _m.group(1), _m.group(2), days=14)
        except Exception:
            # Never block the watchlist add itself if this errors -- same
            # philosophy as the tail->hex resolution above.
            response["ontime_history_14d"] = {"insufficient_data": True}

    return JSONResponse(response, status_code=201)


# ── POST /api/v1/watchlist/trains ─────────────────────────────────────────────

class TrainWatchlistRequest(BaseModel):
    identifier: str
    route_name: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    scheduled_departure: Optional[str] = None
    scheduled_arrival: Optional[str] = None
    auto_remove_at: Optional[str] = None
    notes: Optional[str] = None
    added_by: str = "api"


@router.post("/trains", status_code=201)
async def add_train_watchlist(
    body: TrainWatchlistRequest,
    tier: Tier = Depends(require_admin("watchlist.train.add")),
) -> JSONResponse:
    """Add a transient train watchlist entry. Admin required."""
    ident = body.identifier.strip()
    if not ident:
        raise HTTPException(400, "identifier is required")

    entry_id = _make_id("train", ident)
    now = _now_iso()
    entry = {
        "id": entry_id,
        "entry_type": "train",
        "tier": "transient",
        "identifier": ident,
        "origin": body.origin,
        "destination": body.destination,
        "route_name": body.route_name,
        "scheduled_departure": body.scheduled_departure,
        "scheduled_arrival": body.scheduled_arrival,
        "auto_remove_at": body.auto_remove_at or _default_auto_remove_at(
            body.scheduled_arrival, buffer_hours=3.0),
        "added_at": now,
        "added_by": body.added_by,
        "notes": body.notes,
        "last_event_at": None,
        "last_event_summary": None,
    }
    db.upsert_watchlist_entry(entry)

    route = body.route_name or ""
    origin = body.origin or ""
    dest = body.destination or ""
    route_str = f"{route} " if route else ""
    leg = f"{origin}→{dest}" if origin or dest else ""
    expire_str = ""
    if body.auto_remove_at:
        try:
            exp = datetime.fromisoformat(body.auto_remove_at.replace("Z", "+00:00"))
            expire_str = f" — auto-expire {exp.strftime('%H:%M')} UTC"
        except ValueError:
            expire_str = f" — auto-expire {body.auto_remove_at}"

    _fire_ntfy_dual(
        domain_topic="train-alerts",
        title=f"Watching {route_str}#{ident} {leg}",
        detail_body=f"Train {route_str}#{ident} {leg} added to watchlist{expire_str}",
        dispatch_body=f"Watchlist: {ident} added (transient)",
        priority=2,
    )

    return JSONResponse(entry, status_code=201)


# ── POST /api/v1/watchlist/vessels ────────────────────────────────────────────
# Stub, added 2026-07-21 per operator directive -- yachts/cruise ships,
# identified by MMSI (Maritime Mobile Service Identity, the AIS equivalent
# of a flight's hex). Mirrors the flight/train transient-add shape. Live
# AIS status checking (arrival/dead-sweep parity with flights/trains) is
# NOT wired yet -- this only covers add/store/list/permanent-sync. The
# standalone ais_watcher.py already exists and pulls MMSI matches off the
# local AIS-catcher feed but has never been connected to this watchlist API
# (its own docstring says so); that connection is a follow-up, not part of
# this stub.

class VesselWatchlistRequest(BaseModel):
    identifier: str  # MMSI, 9 digits
    origin: Optional[str] = None       # home port / departure port
    destination: Optional[str] = None  # destination port
    scheduled_arrival: Optional[str] = None
    auto_remove_at: Optional[str] = None
    notes: Optional[str] = None
    added_by: str = "api"


@router.post("/vessels", status_code=201)
async def add_vessel_watchlist(
    body: VesselWatchlistRequest,
    tier: Tier = Depends(require_admin("watchlist.vessel.add")),
) -> JSONResponse:
    """Add a transient vessel (yacht/cruise ship) watchlist entry by MMSI. Admin required."""
    ident = body.identifier.strip()
    if not ident:
        raise HTTPException(400, "identifier (MMSI) is required")
    if not ident.isdigit() or len(ident) != 9:
        raise HTTPException(400, "identifier must be a 9-digit MMSI")

    entry_id = _make_id("vessel", ident)
    now = _now_iso()
    entry = {
        "id": entry_id,
        "entry_type": "vessel",
        "tier": "transient",
        "identifier": ident,
        "origin": body.origin,
        "destination": body.destination,
        "route_name": None,
        "scheduled_departure": None,
        "scheduled_arrival": body.scheduled_arrival,
        "auto_remove_at": body.auto_remove_at or _default_auto_remove_at(
            body.scheduled_arrival, buffer_hours=6.0),
        "added_at": now,
        "added_by": body.added_by,
        "notes": body.notes,
        "last_event_at": None,
        "last_event_summary": None,
    }
    db.upsert_watchlist_entry(entry)

    origin = body.origin or ""
    dest = body.destination or ""
    route = f"{origin}→{dest}" if origin or dest else ""

    _fire_ntfy_dual(
        domain_topic="vessel-alerts",
        title=f"Watching vessel MMSI {ident} {route}",
        detail_body=f"Vessel MMSI {ident} {route} added to watchlist",
        dispatch_body=f"Watchlist: vessel {ident} added (transient)",
        priority=2,
    )

    return JSONResponse(entry, status_code=201)


# ── DELETE /api/v1/watchlist/batch ────────────────────────────────────────────
# 2026-08-16 drift audit: this static-path route MUST be registered before the
# dynamic /{entry_id} route below. Starlette matches in registration order, so
# when /{entry_id} came first (it used to), DELETE /batch was captured by it as
# entry_id="batch" -> remove_watchlist_entry -> 404 "no entry 'batch'", and this
# handler was unreachable dead code. Moved above /{entry_id} to fix the shadow.

class BatchDeleteRequest(BaseModel):
    ids: List[str]


@router.delete("/batch", status_code=200)
async def remove_watchlist_batch(
    body: BatchDeleteRequest,
    tier: Tier = Depends(require_admin("watchlist.batch_remove")),
) -> JSONResponse:
    """Remove multiple watchlist entries by ID array. Admin required."""
    now = _now_iso()
    removed: list[str] = []
    not_found: list[str] = []

    for entry_id in body.ids:
        entry = db.delete_watchlist_entry(entry_id)
        if not entry:
            not_found.append(entry_id)
            continue
        db.insert_watchlist_history(
            entry_id=entry_id,
            entry_type=entry["entry_type"],
            identifier=entry["identifier"],
            event_type="manual_removed",
            event_summary="Batch removed via API",
            event_detail={"removed_by": "api", "batch": True},
            fired_at=now,
        )
        removed.append(entry_id)

    if removed:
        _fire_ntfy_dual(
            domain_topic="dispatch",
            title=f"Watchlist batch: {len(removed)} entr{'y' if len(removed) == 1 else 'ies'} removed",
            detail_body=f"Removed {len(removed)} watchlist entries",
            dispatch_body=f"Watchlist batch removed: {len(removed)} entries",
            priority=2,
        )

    return JSONResponse({
        "removed": removed,
        "not_found": not_found,
        "count": len(removed),
    })


# ── DELETE /api/v1/watchlist/{id} ─────────────────────────────────────────────

@router.delete("/{entry_id}", status_code=204)
async def remove_watchlist_entry(
    entry_id: str,
    tier: Tier = Depends(require_admin("watchlist.entry.remove")),
) -> None:
    """Remove a watchlist entry (either tier). Admin required."""
    entry = db.delete_watchlist_entry(entry_id)
    if not entry:
        raise HTTPException(404, f"Watchlist entry {entry_id!r} not found")

    now = _now_iso()
    db.insert_watchlist_history(
        entry_id=entry_id,
        entry_type=entry["entry_type"],
        identifier=entry["identifier"],
        event_type="manual_removed",
        event_summary="Manually removed via API",
        event_detail={"removed_by": "api"},
        fired_at=now,
    )

    ident = entry["identifier"]
    etype = entry["entry_type"]
    _REMOVE_TOPIC = {"flight": "flight-alerts", "train": "train-alerts",
                     "vessel": "vessel-alerts"}
    _fire_ntfy_dual(
        domain_topic=_REMOVE_TOPIC.get(etype, "dispatch"),
        title=f"Watchlist: {ident} removed",
        detail_body=f"{etype.title()} {ident} removed from watchlist",
        dispatch_body=f"Watchlist: {ident} removed",
        priority=2,
    )


# ── POST /api/v1/watchlist/flights/batch ──────────────────────────────────────

class FlightBatchItem(BaseModel):
    identifier: str
    origin: Optional[str] = None
    destination: Optional[str] = None
    scheduled_departure: Optional[str] = None
    scheduled_arrival: Optional[str] = None
    auto_remove_at: Optional[str] = None
    notes: Optional[str] = None
    added_by: str = "api"
    hex_id: Optional[str] = None  # see FlightWatchlistRequest, 2026-07-22
    registration: Optional[str] = None


class FlightBatchRequest(BaseModel):
    entries: List[FlightBatchItem]
    default_tier: str = "transient"


@router.post("/flights/batch", status_code=201)
async def add_flight_watchlist_batch(
    body: FlightBatchRequest,
    tier: Tier = Depends(require_admin("watchlist.flight.add_batch")),
) -> JSONResponse:
    """Add multiple transient flight watchlist entries. Admin required."""
    now = _now_iso()
    added: list[dict] = []
    errors: list[str] = []

    for item in body.entries:
        ident = item.identifier.strip().upper()
        if not ident:
            errors.append("empty identifier skipped")
            continue
        entry_id = _make_id("flight", ident)
        entry = {
            "id": entry_id,
            "entry_type": "flight",
            "tier": "transient",
            "identifier": ident,
            "origin": (item.origin or "").upper() or None,
            "destination": (item.destination or "").upper() or None,
            "route_name": None,
            "scheduled_departure": item.scheduled_departure,
            "scheduled_arrival": item.scheduled_arrival,
            "auto_remove_at": item.auto_remove_at or _default_auto_remove_at(
                item.scheduled_arrival, buffer_hours=6.0),
            "added_at": now,
            "added_by": item.added_by,
            "notes": item.notes,
            "hex_id": (item.hex_id or "").strip() or None,
            "registration": (item.registration or "").strip() or None,
            "last_event_at": None,
            "last_event_summary": None,
        }
        try:
            db.upsert_watchlist_entry(entry)
            added.append(entry)
        except Exception as e:
            errors.append(f"{ident}: {e}")

    if added:
        identifiers = ", ".join(e["identifier"] for e in added)
        _fire_ntfy_dual(
            domain_topic="flight-alerts",
            title=f"Watchlist batch: {len(added)} flight(s) added",
            detail_body=f"Added {len(added)} flight(s): {identifiers}",
            dispatch_body=f"Watchlist batch: {len(added)} flights added ({identifiers})",
            priority=2,
        )

    return JSONResponse(
        {"added": added, "count": len(added), "errors": errors},
        status_code=201,
    )


# ── POST /api/v1/watchlist/trains/batch ───────────────────────────────────────

class TrainBatchItem(BaseModel):
    identifier: str
    route_name: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    scheduled_departure: Optional[str] = None
    scheduled_arrival: Optional[str] = None
    auto_remove_at: Optional[str] = None
    notes: Optional[str] = None
    added_by: str = "api"


class TrainBatchRequest(BaseModel):
    entries: List[TrainBatchItem]
    default_tier: str = "transient"


@router.post("/trains/batch", status_code=201)
async def add_train_watchlist_batch(
    body: TrainBatchRequest,
    tier: Tier = Depends(require_admin("watchlist.train.add_batch")),
) -> JSONResponse:
    """Add multiple transient train watchlist entries. Admin required."""
    now = _now_iso()
    added: list[dict] = []
    errors: list[str] = []

    for item in body.entries:
        ident = item.identifier.strip()
        if not ident:
            errors.append("empty identifier skipped")
            continue
        entry_id = _make_id("train", ident)
        entry = {
            "id": entry_id,
            "entry_type": "train",
            "tier": "transient",
            "identifier": ident,
            "origin": item.origin,
            "destination": item.destination,
            "route_name": item.route_name,
            "scheduled_departure": item.scheduled_departure,
            "scheduled_arrival": item.scheduled_arrival,
            "auto_remove_at": item.auto_remove_at or _default_auto_remove_at(
                item.scheduled_arrival, buffer_hours=3.0),
            "added_at": now,
            "added_by": item.added_by,
            "notes": item.notes,
            "last_event_at": None,
            "last_event_summary": None,
        }
        try:
            db.upsert_watchlist_entry(entry)
            added.append(entry)
        except Exception as e:
            errors.append(f"{ident}: {e}")

    if added:
        identifiers = ", ".join(e["identifier"] for e in added)
        _fire_ntfy_dual(
            domain_topic="train-alerts",
            title=f"Watchlist batch: {len(added)} train(s) added",
            detail_body=f"Added {len(added)} train(s): {identifiers}",
            dispatch_body=f"Watchlist batch: {len(added)} trains added ({identifiers})",
            priority=2,
        )

    return JSONResponse(
        {"added": added, "count": len(added), "errors": errors},
        status_code=201,
    )


# ── POST /api/v1/watchlist/permanent/batch ────────────────────────────────────

class PermanentFlightItem(BaseModel):
    id: str
    identifier: str
    origin: Optional[str] = None
    destination: Optional[str] = None
    route_name: Optional[str] = None
    notes: Optional[str] = None
    added_by: str = "operator"


class PermanentTrainItem(BaseModel):
    id: str
    identifier: str
    route_name: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    notes: Optional[str] = None
    added_by: str = "operator"


class PermanentVesselItem(BaseModel):
    id: str
    identifier: str  # MMSI, 9 digits
    route_name: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    notes: Optional[str] = None
    added_by: str = "operator"


class PermanentBatchRequest(BaseModel):
    flights: List[PermanentFlightItem] = []
    trains: List[PermanentTrainItem] = []
    vessels: List[PermanentVesselItem] = []


def _merge_permanent_file(filename: str,
                          new_entries: list[dict]) -> tuple[int, int]:
    """
    Atomically merge new_entries into the permanent watchlist JSON file.
    Returns (added, skipped) counts.
    """
    path = PERMANENT_WATCHLIST_DIR / filename
    PERMANENT_WATCHLIST_DIR.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            existing_data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            existing_data = {"watchlist": []}
    else:
        existing_data = {"watchlist": []}

    watchlist: list[dict] = existing_data.get("watchlist", [])
    existing_ids = {e["id"] for e in watchlist if "id" in e}

    now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    added = 0
    skipped = 0
    for entry in new_entries:
        if entry["id"] in existing_ids:
            skipped += 1
            continue
        entry.setdefault("added", now_date)
        watchlist.append(entry)
        existing_ids.add(entry["id"])
        added += 1

    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"watchlist": watchlist}, indent=2))
    os.replace(tmp, path)
    return added, skipped


@router.post("/permanent/batch", status_code=201)
async def add_permanent_watchlist_batch(
    body: PermanentBatchRequest,
    tier: Tier = Depends(require_admin("watchlist.permanent.add_batch")),
) -> JSONResponse:
    """
    Merge entries into permanent watchlist JSON files atomically.
    Existing entries (by id) are skipped — no duplicates, no overwrites.
    Admin required.
    """
    flight_dicts = [f.model_dump() for f in body.flights]
    train_dicts = [t.model_dump() for t in body.trains]
    vessel_dicts = [v.model_dump() for v in body.vessels]

    f_added = f_skipped = t_added = t_skipped = v_added = v_skipped = 0
    if flight_dicts:
        f_added, f_skipped = _merge_permanent_file("permanent_flights.json",
                                                    flight_dicts)
    if train_dicts:
        t_added, t_skipped = _merge_permanent_file("permanent_trains.json",
                                                    train_dicts)
    if vessel_dicts:
        v_added, v_skipped = _merge_permanent_file("permanent_vessels.json",
                                                    vessel_dicts)

    total_added = f_added + t_added + v_added
    if total_added:
        _fire_ntfy_dual(
            domain_topic="dispatch",
            title=f"Permanent watchlist: {total_added} entr{'y' if total_added == 1 else 'ies'} added",
            detail_body=(f"Permanent watchlist updated: {f_added} flight(s), "
                         f"{t_added} train(s), {v_added} vessel(s) added. "
                         f"{f_skipped + t_skipped + v_skipped} skipped (duplicates)."),
            dispatch_body=(f"Permanent watchlist: +{f_added} flights, "
                           f"+{t_added} trains, +{v_added} vessels"),
            priority=2,
        )

    return JSONResponse({
        "flights": {"added": f_added, "skipped": f_skipped},
        "trains": {"added": t_added, "skipped": t_skipped},
        "vessels": {"added": v_added, "skipped": v_skipped},
        "total_added": total_added,
    }, status_code=201)
