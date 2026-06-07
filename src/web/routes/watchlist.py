"""
web.routes.watchlist — Permanent + transient watchlist REST API.

GET  /api/v1/watchlist              List all active entries (Tier 0)
GET  /api/v1/watchlist/history      Recent events (Tier 0)
POST /api/v1/watchlist/flights      Add transient flight entry (Admin)
POST /api/v1/watchlist/trains       Add transient train entry (Admin)
DELETE /api/v1/watchlist/{id}       Remove an entry (Admin)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from auth.auth import Tier, require_admin
from common import db
from shared.watchlist import _fire_ntfy_dual, PERMANENT_WATCHLIST_DIR

router = APIRouter(prefix="/api/v1/watchlist", tags=["watchlist"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_id(entry_type: str, identifier: str) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    ident_slug = identifier.lower().replace(" ", "-")
    return f"wl-{entry_type}-{ident_slug}-{date_str}"


# ── GET /api/v1/watchlist ─────────────────────────────────────────────────────

@router.get("")
async def list_watchlist_entries() -> JSONResponse:
    """List all active watchlist entries (permanent + transient). Tier 0."""
    entries = db.get_watchlist_entries()
    return JSONResponse({"entries": entries, "count": len(entries)})


# ── GET /api/v1/watchlist/history ─────────────────────────────────────────────

@router.get("/history")
async def watchlist_history(limit: int = 50) -> JSONResponse:
    """Recent watchlist events. Tier 0."""
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


@router.post("/flights", status_code=201)
async def add_flight_watchlist(
    body: FlightWatchlistRequest,
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    """Add a transient flight watchlist entry. Admin required."""
    ident = body.identifier.strip().upper()
    if not ident:
        raise HTTPException(400, "identifier is required")

    entry_id = _make_id("flight", ident)
    now = _now_iso()
    entry = {
        "id": entry_id,
        "entry_type": "flight",
        "tier": "transient",
        "identifier": ident,
        "origin": (body.origin or "").upper() or None,
        "destination": (body.destination or "").upper() or None,
        "route_name": None,
        "scheduled_departure": body.scheduled_departure,
        "scheduled_arrival": body.scheduled_arrival,
        "auto_remove_at": body.auto_remove_at,
        "added_at": now,
        "added_by": body.added_by,
        "notes": body.notes,
        "last_event_at": None,
        "last_event_summary": None,
    }
    db.upsert_watchlist_entry(entry)

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

    _fire_ntfy_dual(
        domain_topic="flight-alerts",
        title=f"Watching {ident} {route}",
        detail_body=f"Flight {ident} {route} added to watchlist{expire_str}",
        dispatch_body=f"Watchlist: {ident} added (transient)",
        priority=2,
    )

    return JSONResponse(entry, status_code=201)


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
    tier: Tier = Depends(require_admin),
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
        "auto_remove_at": body.auto_remove_at,
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


# ── DELETE /api/v1/watchlist/{id} ─────────────────────────────────────────────

@router.delete("/{entry_id}", status_code=204)
async def remove_watchlist_entry(
    entry_id: str,
    tier: Tier = Depends(require_admin),
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
    _fire_ntfy_dual(
        domain_topic="flight-alerts" if etype == "flight" else "train-alerts",
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


class FlightBatchRequest(BaseModel):
    entries: List[FlightBatchItem]
    default_tier: str = "transient"


@router.post("/flights/batch", status_code=201)
async def add_flight_watchlist_batch(
    body: FlightBatchRequest,
    tier: Tier = Depends(require_admin),
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
            "auto_remove_at": item.auto_remove_at,
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
    tier: Tier = Depends(require_admin),
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
            "auto_remove_at": item.auto_remove_at,
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


class PermanentBatchRequest(BaseModel):
    flights: List[PermanentFlightItem] = []
    trains: List[PermanentTrainItem] = []


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
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    """
    Merge entries into permanent watchlist JSON files atomically.
    Existing entries (by id) are skipped — no duplicates, no overwrites.
    Admin required.
    """
    flight_dicts = [f.model_dump() for f in body.flights]
    train_dicts = [t.model_dump() for t in body.trains]

    f_added = f_skipped = t_added = t_skipped = 0
    if flight_dicts:
        f_added, f_skipped = _merge_permanent_file("permanent_flights.json",
                                                    flight_dicts)
    if train_dicts:
        t_added, t_skipped = _merge_permanent_file("permanent_trains.json",
                                                    train_dicts)

    total_added = f_added + t_added
    if total_added:
        _fire_ntfy_dual(
            domain_topic="dispatch",
            title=f"Permanent watchlist: {total_added} entr{'y' if total_added == 1 else 'ies'} added",
            detail_body=(f"Permanent watchlist updated: {f_added} flight(s), "
                         f"{t_added} train(s) added. "
                         f"{f_skipped + t_skipped} skipped (duplicates)."),
            dispatch_body=(f"Permanent watchlist: +{f_added} flights, "
                           f"+{t_added} trains"),
            priority=2,
        )

    return JSONResponse({
        "flights": {"added": f_added, "skipped": f_skipped},
        "trains": {"added": t_added, "skipped": t_skipped},
        "total_added": total_added,
    }, status_code=201)


# ── DELETE /api/v1/watchlist/batch ────────────────────────────────────────────

class BatchDeleteRequest(BaseModel):
    ids: List[str]


@router.delete("/batch", status_code=200)
async def remove_watchlist_batch(
    body: BatchDeleteRequest,
    tier: Tier = Depends(require_admin),
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
