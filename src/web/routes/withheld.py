"""web.routes.withheld -- pass and master-token routes for the runner (2026-11-02).

  POST /api/v1/withheld/pair     {code}             Tier 1+ (the runner's service token):
                                                    exchange a signed one-time pairing code
                                                    for a pass or master token; the runner
                                                    keeps it in an httponly cookie.
  POST /api/v1/withheld/forget                      revoke the token sent in X-CTDC-Withheld-Pass.
  GET  /api/v1/withheld/whoami                      what that token reveals right now.
  POST /api/v1/withheld/screen   {category, items}  Tier 1+: which items the caller may NOT
                                                    see -- the runner's local receivers (ADS-B,
                                                    AIS) have no database of their own.

Issuing, minting, list upkeep and the kill switch are operator-only
(scripts/withheld.sh, inside this container); nothing here issues a token without
the signed approval (and, for a master, the verified GPG signature).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auth.auth import Tier, require_tier, resolve_withheld
from common import withheld

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/withheld", tags=["withheld"])


class PairBody(BaseModel):
    code: str = Field(..., min_length=8, max_length=64)


class ScreenBody(BaseModel):
    category: str = Field(..., max_length=32)
    items: list[dict] = Field(default_factory=list, max_length=5000)


@router.post("/pair")
def withheld_pair(body: PairBody, request: Request, tier: Tier = Depends(require_tier(Tier.T1))) -> JSONResponse:
    if request.headers.get("X-CTDI-Public") == "1":
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        out = withheld.pair(body.code)
    except withheld.WithheldError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    return JSONResponse(out)


@router.post("/forget")
def withheld_forget(request: Request) -> JSONResponse:
    raw = request.headers.get(withheld.PASS_HEADER) or ""
    return JSONResponse({"revoked": withheld.revoke_token(raw) if raw else 0})


@router.get("/whoami")
def withheld_whoami(held=Depends(resolve_withheld)) -> JSONResponse:
    v = withheld.view_of(held)
    return JSONResponse({"categories": sorted(c for c in v.grants if v.allowed(c)), "master": v.master,
                         "device": v.device if v.grants else None, "frozen": sorted(v.frozen)})


@router.post("/screen")
def withheld_screen(body: ScreenBody, tier: Tier = Depends(require_tier(Tier.T1)),
                    held=Depends(resolve_withheld)) -> JSONResponse:
    try:
        withheld.category(body.category)
    except withheld.WithheldError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    items = [{**{k: v for k, v in a.items() if isinstance(v, (str, int))}, "_i": i}
             for i, a in enumerate(body.items) if isinstance(a, dict)]
    try:
        kept, n = withheld.screen(held, body.category, items)
    except withheld.WithheldUnavailable:
        raise HTTPException(status_code=503, detail="privacy filter unavailable")
    keep = {k["_i"] for k in kept}
    return JSONResponse({"withhold": [i for i in range(len(body.items)) if i not in keep], "withheld": n})
