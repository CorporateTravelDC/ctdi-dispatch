"""
web.routes.webhooks -- Inbound webhook receivers for third-party dispatch/PBX
integrations (LimoAnywhere, RingCentral, 3CX).

POST /webhooks/limoanywhere/reservations   Reservation events (create/update/cancel)
POST /webhooks/ringcentral/events          Call/SMS/voicemail events (+ validation handshake)
POST /webhooks/3cx/events                  Call control events

All three are credential-gated: if the corresponding *_WEBHOOK_SECRET env var
is unset, the endpoint returns 503 rather than silently accepting
unauthenticated traffic. This mirrors the existing FAA_NOTAM_API_KEY gating
pattern in common/config.py -- the code ships ready and activates the moment
real credentials land in dispatch-secrets.env, nothing else to wire up.

Auth model: a shared-secret header (X-Webhook-Secret), checked per source
against its own env var. This is deliberately NOT each platform's native
signature/HMAC scheme -- verifying those precisely requires a live developer
sandbox for each vendor, which we don't have yet. A shared secret known only
to us and the sender is a real, standard auth mechanism (all three platforms
support custom outbound headers), just not the vendor-native one. Tighten to
native signature verification once real sandbox access exists for each.

RingCentral additionally requires echoing its Validation-Token header
verbatim on the one-time subscription verification request before it will
send any real event traffic -- handled unconditionally ahead of secret checks
below, since that handshake carries no payload and authorizes nothing on its
own.
"""
from __future__ import annotations

import json
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from common import config, db
from shared.watchlist import _fire_ntfy_dual

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _check_secret(source: str, provided: Optional[str], env_key: str) -> None:
    expected = config.get(env_key, "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=f"{source} webhook not configured -- set {env_key} in "
                   f"dispatch-secrets.env to activate.",
        )
    # 2026-08-16 drift audit: constant-time compare -- a plain `!=` on the
    # shared secret is a timing side-channel. Matches the compare_digest the
    # board-write path (web/main.py) already uses for the same class of check.
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook secret.")


# -- LimoAnywhere -------------------------------------------------------------

@router.post("/limoanywhere/reservations")
async def limoanywhere_reservation(
    request: Request,
    x_webhook_secret: Optional[str] = Header(default=None),
) -> JSONResponse:
    """
    Receives LimoAnywhere Customer API reservation webhook deliveries.
    Body: full reservation snapshot + a  field naming the
    event (e.g. reservation.created, reservation.updated, reservation.cancelled).
    """
    _check_secret("LimoAnywhere", x_webhook_secret, "LIMOANYWHERE_WEBHOOK_SECRET")
    payload = await request.json()
    event_type = payload.get("reservation_event", "unknown")
    external_ref = str(payload.get("id") or payload.get("reservation_id") or "")

    db.insert_webhook_event(
        source="limoanywhere",
        event_type=event_type,
        external_ref=external_ref,
        payload=json.dumps(payload),
    )

    passenger = payload.get("passenger") or {}
    client_name = passenger.get("name") or payload.get("passenger_name") or "unknown client"
    _fire_ntfy_dual(
        domain_topic="reservations",
        title="LimoAnywhere reservation event",
        detail_body=f"{event_type}: {client_name} -- ref {external_ref}",
        dispatch_body=f"[LimoAnywhere] {event_type} ({client_name})",
        priority=3,
    )
    return JSONResponse({"status": "accepted", "event": event_type})


# -- RingCentral ---------------------------------------------------------------

@router.post("/ringcentral/events")
async def ringcentral_event(
    request: Request,
    validation_token: Optional[str] = Header(default=None, alias="Validation-Token"),
    x_webhook_secret: Optional[str] = Header(default=None),
):
    if validation_token:
        # One-time subscription verification handshake -- echo back verbatim.
        return PlainTextResponse("", headers={"Validation-Token": validation_token})

    _check_secret("RingCentral", x_webhook_secret, "RINGCENTRAL_WEBHOOK_SECRET")
    payload = await request.json()
    event_type = payload.get("event", "unknown")
    external_ref = str(payload.get("uuid") or "")

    db.insert_webhook_event(
        source="ringcentral",
        event_type=event_type,
        external_ref=external_ref,
        payload=json.dumps(payload),
    )

    _fire_ntfy_dual(
        domain_topic="calls",
        title="RingCentral event",
        detail_body=f"{event_type} -- ref {external_ref}",
        dispatch_body=f"[RingCentral] {event_type}",
        priority=3,
    )
    return JSONResponse({"status": "accepted", "event": event_type})


# -- 3CX ------------------------------------------------------------------------

@router.post("/3cx/events")
async def threecx_event(
    request: Request,
    x_webhook_secret: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Receives 3CX Call Control / WebSocket-bridged call events."""
    _check_secret("3CX", x_webhook_secret, "THREECX_WEBHOOK_SECRET")
    payload = await request.json()
    event_type = payload.get("event_type") or payload.get("Event") or "unknown"
    external_ref = str(payload.get("call_id") or payload.get("CallId") or "")

    db.insert_webhook_event(
        source="3cx",
        event_type=event_type,
        external_ref=external_ref,
        payload=json.dumps(payload),
    )

    _fire_ntfy_dual(
        domain_topic="calls",
        title="3CX call event",
        detail_body=f"{event_type} -- ref {external_ref}",
        dispatch_body=f"[3CX] {event_type}",
        priority=3,
    )
    return JSONResponse({"status": "accepted", "event": event_type})
