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

2026-09-21: LimoAnywhere reservations now try to extract a flight/train
identifier and auto-add it to the real watchlist (web.routes.watchlist,
POST /api/v1/watchlist/{flights,trains}) -- the piece the README's design
language always described ("extracts the flight number or train number,
and calls CTDI's watchlist API") but was never built; log+notify was the
whole implementation until now. LimoAnywhere's real Customer API payload
schema isn't available in this environment (same sandbox gap noted above
for the auth scheme) -- the field-name guesses in _extract_flight_identifier/
_extract_train_identifier are reasonable, common patterns, NOT verified
against a real vendor payload. If they miss, the existing log+notify
behavior is the correct fallback, not a bug. RingCentral/3CX stay
event-log + notify only -- call events don't carry trip data the way a
reservation does.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from typing import Optional

import requests
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from common import config, db
from shared.watchlist import _fire_ntfy_dual

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# -- Flight/train extraction + auto-watchlist (LimoAnywhere only) -----------

# IATA -> ICAO carrier prefix, same table flight-hifi-track's skill (Step 1a)
# uses for callsign normalization -- the watchlist API's own identity
# resolution (FDPS cross-check, airplanes.live) expects the ICAO form, and
# the immutable rule from that skill applies here too: never the bare IATA
# form, never a bare flight number.
_IATA_TO_ICAO_CARRIER = {
    "DL": "DAL", "UA": "UAL", "AA": "AAL", "BA": "BAW",
    "KL": "KLM", "AF": "AFR", "LH": "DLH",
}
_CALLSIGN_SHAPE_RE = re.compile(r"^([A-Za-z]{2,3})\s?(\d{1,4}[A-Za-z]?)$")


def _normalize_flight_identifier(raw: str) -> Optional[str]:
    """
    Best-effort IATA -> ICAO normalization. Returns None (never a wrong
    guess) for anything that doesn't match a plausible callsign shape. An
    unrecognized 2-letter prefix is passed through uppercased rather than
    dropped -- a real-but-unmapped carrier code is still a better
    watchlist add than silence; the watchlist API's own FDPS cross-check
    at add-time is the actual verification step, not this normalization.
    """
    if not raw:
        return None
    m = _CALLSIGN_SHAPE_RE.match(raw.strip())
    if not m:
        return None
    prefix, num = m.group(1).upper(), m.group(2).upper()
    if len(prefix) == 2:
        prefix = _IATA_TO_ICAO_CARRIER.get(prefix, prefix)
    return f"{prefix}{num}"


def _dig(payload: dict, *keys: str) -> Optional[str]:
    """Check top-level keys, then the same keys nested under a few common
    reservation-payload wrapper objects. Unverified-against-real-vendor-
    payload, see module docstring."""
    for key in keys:
        v = payload.get(key)
        if v:
            return str(v)
    for wrapper in ("trip", "itinerary", "transportation", "flight_info",
                    "reservation_detail"):
        nested = payload.get(wrapper)
        if isinstance(nested, dict):
            for key in keys:
                v = nested.get(key)
                if v:
                    return str(v)
    return None


def _extract_flight_identifier(payload: dict) -> Optional[str]:
    raw = _dig(payload, "flight_number", "flight_no", "flightNumber",
              "arrival_flight", "departure_flight", "flight")
    return _normalize_flight_identifier(raw) if raw else None


def _extract_train_identifier(payload: dict) -> Optional[str]:
    raw = _dig(payload, "train_number", "train_num", "trainNumber", "train")
    if not raw:
        return None
    # Amtrak identifiers are bare numbers, not ICAO-style callsigns --
    # verified live against the real feed by nec-train-hifi-track's skill
    # (2026-07-29 schema correction), no carrier-prefix normalization
    # applies here.
    digits = re.sub(r"\D", "", raw)
    return digits or None


def _add_to_watchlist(entry_type: str, identifier: str, source: str,
                      notes: str) -> None:
    """
    Best-effort call into this SAME service's own /api/v1/watchlist/{type}
    endpoint (web.routes.watchlist) over loopback -- not the public tunnel,
    so the Cloudflare Access gate on the public hostname doesn't apply here.
    Uses the admin bearer token already loaded via dispatch-secrets.env
    (DISPATCH_ADMIN_TOKEN), the same auth model every other admin caller in
    this codebase uses -- not a new credential. Never raises: a watchlist-add
    failure must not turn a successfully-received, already-persisted webhook
    (db.insert_webhook_event() already ran) into a 500.
    """
    token = config.get("DISPATCH_ADMIN_TOKEN", "")
    if not token:
        log.warning("webhook auto-watchlist skipped for %s %s -- "
                   "DISPATCH_ADMIN_TOKEN not configured", entry_type, identifier)
        return
    try:
        requests.post(
            f"http://127.0.0.1:8000/api/v1/watchlist/{entry_type}",
            json={"identifier": identifier, "added_by": source, "notes": notes},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
    except Exception as e:
        log.warning("webhook auto-watchlist call failed for %s %s: %s",
                   entry_type, identifier, e)


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

    # 2026-09-21: extract + auto-watchlist -- see module docstring. Flight
    # checked before train since a ground-transport reservation is far more
    # likely to reference an inbound/outbound flight than a train; a
    # payload naming both is unusual enough that "flight wins" is a
    # reasonable tiebreak, not a real design decision.
    flight_id = _extract_flight_identifier(payload)
    train_id = _extract_train_identifier(payload)
    tracked_note = None
    if flight_id:
        _add_to_watchlist("flights", flight_id, "limoanywhere-webhook",
                          f"Auto-added from LimoAnywhere reservation {external_ref}")
        tracked_note = f"flight {flight_id}"
    elif train_id:
        _add_to_watchlist("trains", train_id, "limoanywhere-webhook",
                          f"Auto-added from LimoAnywhere reservation {external_ref}")
        tracked_note = f"train {train_id}"

    passenger = payload.get("passenger") or {}
    client_name = passenger.get("name") or payload.get("passenger_name") or "unknown client"
    tracked_suffix = f" -- tracking {tracked_note}" if tracked_note else ""
    _fire_ntfy_dual(
        domain_topic="reservations",
        title="LimoAnywhere reservation event",
        detail_body=f"{event_type}: {client_name} -- ref {external_ref}{tracked_suffix}",
        dispatch_body=f"[LimoAnywhere] {event_type} ({client_name}){tracked_suffix}",
        priority=3,
    )
    response = {"status": "accepted", "event": event_type}
    if tracked_note:
        response["auto_tracked"] = tracked_note
    return JSONResponse(response)


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
