"""
web/routes/assignments.py -- gate / terminal / baggage assignments per flight leg
(2026-10-18, operator, gate batch; Phase A hard gate H2).

GET  /api/v1/flights/{ident}/assignments          ?origin=&destination=&date=YYYY-MM-DD
GET  /api/v1/flights/{ident}/{date}/assignments   the dated-leg form
     Tier 1 (same as the watchlist reads: which flight someone asked about is
     not public). ALWAYS 200: a field no source has is reported `unknown`,
     never a 404 -- so a FIDS outage cannot turn into "no gate answer at all".
     Per field (arrival_gate, arrival_terminal, arrival_baggage,
     departure_gate): value, status (observed / provisional / conflicting /
     unknown; earlier values listed as superseded), source, observed_at (UTC),
     freshness. No time, status or tail from FIDS is ever in the answer; a
     LADD-listed flight answers `unknown` for everything, the same as no data.
POST /api/v1/gate-observations/acars                Admin (audited)
     Batches from the ACARS watcher's gate forwarder (parsed fields + envelope;
     no message text, no registration). Answers counts only.
"""
from __future__ import annotations

import time
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from auth.auth import Tier, require_admin, require_tier
from common import flight_instance as fi
from common import gate_assignments as ga

router = APIRouter(tags=["assignments"])

_DATE = r"^\d{4}-\d{2}-\d{2}$"


def _answer(ident: str, origin: Optional[str], destination: Optional[str],
            date: Optional[str]) -> dict:
    now = time.time()
    ident_u = (ident or "").strip().upper()
    out: dict[str, Any] = {
        "ident": ident_u,
        "query": {"origin": fi.norm_airport(origin), "destination": fi.norm_airport(destination),
                  "date": date},
        "legs": [],
        "generated_at": ga._iso(now),
        "precedence": "SWIM > ACARS > AeroAPI (fallback) > MWAA FIDS (DCA/IAD)",
    }
    parsed = fi.parse_flight_ident(ident_u)
    blocked = True
    if parsed:
        blocked = ga.ladd_blocked([ident_u, f"{parsed[0]}{parsed[1]}"])
    legs = [] if (blocked or not parsed) else ga.legs_for(ident_u, origin, destination, date, now)
    for leg in legs:
        out["legs"].append({
            "leg_key": leg["leg_key"],
            "operating": f"{leg['carrier']}{leg['flight_num']}",
            "origin": leg.get("origin"), "destination": leg.get("destination"),
            "sched_date": leg.get("sched_date"),
            "fields": ga.leg_assignments(leg["leg_key"], now),
        })
    out["fields"] = out["legs"][0]["fields"] if out["legs"] else ga.unknown_fields()
    if not parsed:
        out["note"] = "not a flight number (carrier + number, e.g. UA1234 or UAL1234)"
    elif not out["legs"]:
        out["note"] = "no source has an assignment for this flight yet"
    elif len(out["legs"]) > 1:
        out["note"] = "several legs match; pass origin/destination/date to narrow"
    return out


@router.get("/api/v1/flights/{ident}/assignments")
def flight_assignments(
    ident: str,
    origin: Optional[str] = Query(default=None, max_length=4),
    destination: Optional[str] = Query(default=None, max_length=4),
    date: Optional[str] = Query(default=None, pattern=_DATE, description="scheduled departure date (UTC)"),
    tier: Tier = Depends(require_tier(Tier.T1)),
) -> JSONResponse:
    return JSONResponse(_answer(ident, origin, destination, date))


@router.get("/api/v1/flights/{ident}/{date}/assignments")
def flight_leg_assignments(
    ident: str,
    date: str,
    origin: Optional[str] = Query(default=None, max_length=4),
    destination: Optional[str] = Query(default=None, max_length=4),
    tier: Tier = Depends(require_tier(Tier.T1)),
) -> JSONResponse:
    import re
    if not re.match(_DATE, date or ""):
        out = _answer(ident, origin, destination, None)
        out["legs"], out["fields"] = [], ga.unknown_fields()
        out["note"] = "date must be YYYY-MM-DD (UTC scheduled departure)"
        return JSONResponse(out)
    return JSONResponse(_answer(ident, origin, destination, date))


class AcarsGateItem(BaseModel):
    source: str
    hit: dict
    label: Optional[str] = None
    timestamp: Any = None
    direction: Optional[str] = None
    flight: Optional[str] = None
    msg_id: Optional[str] = None


class AcarsGateBatch(BaseModel):
    items: List[AcarsGateItem]


@router.post("/api/v1/gate-observations/acars")
def ingest_acars_gate(
    body: AcarsGateBatch,
    tier: Tier = Depends(require_admin("gate.acars.ingest")),
) -> JSONResponse:
    items = [i.model_dump() if hasattr(i, "model_dump") else i.dict() for i in body.items[:200]]
    return JSONResponse({"ok": True, "counts": ga.ingest_acars(items)})
