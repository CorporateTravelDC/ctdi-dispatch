"""
dispatch-runner -- internal operational PWA backend.
FastAPI on port 8001. Tailscale-gated. Serves React static build + API proxy.

Signal proxy fallback chain:
  VDL2 / ACARS / HFDL: local acarshub (:9081)
                        -> api.jumpseat.acarsdrama.com/v1 (acarsdrama Jumpseat)
                        -> api.airframes.io (airframes.io, secondary fallback)
  AIS:                  local AIS-catcher (:8110) -> Kpler Maritime 2.0 GraphQL
  All external fallbacks: 250nm radius centered on KDCA (38.8816, -77.0910)
"""
import asyncio
import base64
import datetime
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import random
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

log = logging.getLogger(__name__)

# ── Configuration -----------------------------------------------------------
DISPATCH_BASE      = os.getenv("DISPATCH_BASE_URL",        "http://127.0.0.1:8000")

# Demo-playback gating. Only ever true on the corporatetraveldc-runner-demo
# Quadlet instance (port 8005) -- the live ops.example.com
# instance never sets this, so none of the code guarded by it can affect
# real operational traffic. DEMO_SESSION_SECRET must match the value
# demo.profiles (src/demo/profiles.py) signs tokens with -- both read it
# from the same dispatch-secrets.env.
DEMO_MODE           = os.getenv("DEMO_MODE", "false").strip().lower() == "true"
DEMO_SESSION_SECRET = os.getenv("DEMO_SESSION_SECRET", "")
DEMO_SESSION_COOKIE = "ctdc_demo_session"
NTFY_URL           = os.getenv("NTFY_URL",                  "http://host.containers.internal:2586")
NTFY_TOKEN         = os.getenv("NTFY_TOKEN",                "")
ULTRAFEEDER_URL    = os.getenv("ULTRAFEEDER_URL",           "http://127.0.0.1:8080/data/aircraft.json")
ACARSHUB_URL       = os.getenv("ACARSHUB_URL",             "http://127.0.0.1:9081")
AIS_CATCHER_URL    = os.getenv("AIS_CATCHER_URL",          "http://127.0.0.1:8110")
AIRPLANES_LIVE     = "https://api.airplanes.live/v2"

# acarsdrama Jumpseat -- primary external fallback for VDL2/ACARS/HFDL
# Endpoint: https://api.jumpseat.acarsdrama.com/v1/messages/search
# Auth: Authorization: Bearer sk_adjs_...
# source param: vdl2 | acars | hfdl | messages (all types)
ACARSDRAMA_BASE    = os.getenv("ACARSDRAMA_BASE_URL",       "https://api.jumpseat.acarsdrama.com/v1")
ACARSDRAMA_TOKEN   = (os.getenv("ACARSDRAMA_JUMPSEAT_TOKEN") or
                      os.getenv("AIRFRAMES_JUMPSEAT_TOKEN") or "")

# airframes.io -- secondary external fallback (keep both)
# Only used if acarsdrama is unavailable or returns no results
AIRFRAMES_BASE     = os.getenv("AIRFRAMES_BASE_URL",        "https://api.airframes.io/v1")
# Fixed 2026-08-03: airframes.io docs (docs.airframes.io/api) confirm the
# documented, stable base is .../v1 -- the bare host DOES respond for
# backward compat but /v1 is what should be used for new integrations.
# Was missing here; combined with AIRFRAMES_TOKEN being unset, this meant
# the airframes.io fallback tier for VDL2/ACARS/HFDL never actually ran.
AIRFRAMES_TOKEN    = os.getenv("AIRFRAMES_TOKEN",            "")

KPLER_GRAPHQL_URL    = "https://api.sml.kpler.com/graphql"
KPLER_MARITIME_TOKEN = os.getenv("KPLER_MARITIME_API_TOKEN", "")  # Maritime 2.0 GraphQL Bearer token
AIS_MT_WIDGET_KEY  = os.getenv("AIS_MARINETRAFFIC_KEY",     "")  # widget embed key (widget_id param)
AIS_AISHUB_ID      = os.getenv("AIS_AISHUB_ID",             "")
AIS_AISHUB_BASE    = "http://data.aishub.net/ws.php"
TAILSCALE_CIDR     = ipaddress.ip_network("100.64.0.0/10")
STATIC_DIR         = os.getenv("STATIC_DIR",                "/app/static")
SSE_INTERVAL_SEC   = int(os.getenv("SSE_INTERVAL_SEC",      "30"))

# ── Runner service token (cert tier) ----------------------------------------
# Used by the runner to call Tier-1-gated dispatch endpoints on behalf of the
# frontend (e.g. /api/v1/tfr-enriched).  The token is injected server-side so
# the browser never sees it.  Set in dispatch-secrets.env.
RUNNER_ENRICHED_TOKEN = os.getenv("RUNNER_ENRICHED_TOKEN", "")

# ── Dispatch AI chat (Local/Cloud LLM) -------------------------------------
# These local inference endpoints are also exposed as portable MCP tools.
# MCP server: https://github.com/CorporateTravelDC/corporatetravel-dispatch-mcp
# Use with Claude Code, Cline, Cursor, Zed, Windsurf, or Open WebUI via mcpo.
# Resolution order: local data → Open WebUI proxy → Ollama direct fallback.
# Both corporatetraveldc-pi5-chat and corporatetraveldc-pi5-osint are Modelfile wrappers on mistral-nemo:latest.
# llama3.2:3b removed. Operator may override per-request via "/model <name> <query>".
OLLAMA_BASE_URL    = os.getenv("OLLAMA_BASE_URL",   "")              # e.g. http://host.containers.internal:11434
OPENWEBUI_URL      = os.getenv("OPENWEBUI_URL",     "")              # e.g. http://127.0.0.1:3000
OPENWEBUI_API_KEY  = os.getenv("OPENWEBUI_API_KEY", "")              # sk-... bearer token
OLLAMA_CHAT_MODEL  = os.getenv("OLLAMA_CHAT_MODEL",  "corporatetraveldc-pi5-chat:latest")  # dispatch drawer (mistral-nemo)
OLLAMA_OSINT_MODEL = os.getenv("OLLAMA_OSINT_MODEL", "corporatetraveldc-pi5-osint:latest") # OSINT narrative (mistral-nemo)
OLLAMA_MODEL       = os.getenv("OLLAMA_MODEL",      OLLAMA_CHAT_MODEL) # backward-compat alias

# Chat endpoint + auth headers: prefer Open WebUI's Ollama proxy; fall back to Ollama direct.
def _chat_endpoint() -> str:
    if OPENWEBUI_URL:
        return f"{OPENWEBUI_URL.rstrip('/')}/ollama/api/chat"
    if OLLAMA_BASE_URL:
        return f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    return ""

def _chat_headers() -> dict:
    if OPENWEBUI_URL and OPENWEBUI_API_KEY:
        return {"Authorization": f"Bearer {OPENWEBUI_API_KEY}"}
    return {}

# Default center: KDCA
DEFAULT_LAT  = 38.8816
DEFAULT_LON  = -77.0910
DEFAULT_DIST = 250  # nm

app = FastAPI(title="dispatch-runner", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ── Helpers ------------------------------------------------------------------

def _client_ip(request: Request) -> str:
    # Cloudflare sets CF-Connecting-IP authoritatively at its edge -- it is
    # not client-spoofable and is present on every request that actually
    # traversed the Cloudflare tunnel (the public ops.example.com
    # hostname). Prefer it over X-Forwarded-For, which for tunnel traffic
    # reduces to cloudflared's own loopback hop into nginx and is not a
    # reliable signal of the real origin.
    cf_ip = request.headers.get("CF-Connecting-IP", "").strip()
    if cf_ip:
        return cf_ip
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""

def _is_tailscale(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in TAILSCALE_CIDR
    except ValueError:
        return False


_TRUSTED_NETS = [
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]


def _is_trusted(request: Request) -> bool:
    # BUG FIX 2026-07-21: when a request arrives via the Cloudflare tunnel
    # (ops.example.com), CF-Connecting-IP is the ONLY signal we
    # trust for real origin. Previously this function also checked
    # request.client.host and a naively-parsed X-Forwarded-For, both of
    # which -- for tunnel traffic -- resolve to cloudflared's own loopback
    # hop into nginx (127.0.0.1), which is in _TRUSTED_NETS. Whenever
    # Cloudflare's edge didn't additionally forward a usable X-Forwarded-For
    # chain, that loopback hop got treated as a trusted LAN origin, which is
    # exactly why the public Ops hostname intermittently showed admin/search
    # as available when it should not have been. If CF-Connecting-IP is
    # present, we decide trust from that value alone and never fall
    # through to the loopback-derived candidates.
    cf_ip = request.headers.get("CF-Connecting-IP", "").strip()
    if cf_ip:
        try:
            addr = ipaddress.ip_address(cf_ip)
            trusted = any(addr in net for net in _TRUSTED_NETS)
        except ValueError:
            trusted = False
        if not trusted:
            log.warning("runner: untrusted (cloudflare) cf_ip=%s path=%s", cf_ip, request.url.path)
        return trusted

    direct = request.client.host if request.client else ""
    xff = _client_ip(request)
    for candidate in filter(None, [direct, xff]):
        try:
            addr = ipaddress.ip_address(candidate)
            if any(addr in net for net in _TRUSTED_NETS):
                return True
        except ValueError:
            pass
    log.warning("runner: untrusted direct=%s xff=%s path=%s", direct, xff, request.url.path)
    return False


def _verify_demo_session(token: str | None) -> dict | None:
    """Stateless HMAC verify of a demo session token issued by
    demo.profiles.issue_session_token() (src/demo/profiles.py -- the
    signing side of truth). Duplicated here rather than imported because
    the runner and demo-api are separate containers/build contexts; this
    is deliberately a small, stable, stdlib-only algorithm (~15 lines) so
    the duplication risk is low. Requires DEMO_SESSION_SECRET to match on
    both sides (both read it from dispatch-secrets.env). Returns the
    decoded payload (id/label/window_days/speed/exp) if valid and
    unexpired, else None -- callers never distinguish "missing", "bad
    signature", and "expired"; all three just mean "not authenticated"."""
    if not token or not DEMO_SESSION_SECRET:
        return None
    try:
        payload_b, sig_b = token.encode().split(b".", 1)
        expected_sig = hmac.new(DEMO_SESSION_SECRET.encode(), payload_b, hashlib.sha256).digest()
        expected_sig_b = base64.urlsafe_b64encode(expected_sig).rstrip(b"=")
        if not hmac.compare_digest(sig_b, expected_sig_b):
            return None
        pad = b"=" * (-len(payload_b) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b + pad))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# UPDATED 2026-07-21 per operator direction: Ops (the public Cloudflare
# hostname) gets ZERO admin capability, full stop -- not "admin data is
# hidden," but "admin actions are unreachable regardless of what token is
# presented." Previously this middleware only checked the bare /admin
# prefix, which nothing on this service actually serves directly -- every
# real admin action reaches this container via the generic
# /api/dispatch/{path} proxy in proxy_dispatch() below, as
# /api/dispatch/admin/... . That meant a caller with a valid admin token
# could perform real admin actions straight through the public hostname,
# which is exactly what the operator wants closed off. Two things are now
# blocked pre-emptively, before the request ever reaches the proxy or a
# downstream token check, unless the request itself is tailnet/LAN-
# originating (_is_trusted):
#   1. Any request (GET or otherwise) to /api/dispatch/admin/*
#   2. Any non-GET (mutating) request to /api/dispatch/api/v1/*
# A trusted-origin request still needs its own valid token for the
# downstream dispatch-web service to actually accept it -- this
# middleware only enforces the network-origin half of "tailnet AND
# token," per operator direction ("100.x.x.x:8001 will have all
# access, assuming a valid token").
_ADMIN_PROXY_PREFIX = "/api/dispatch/admin"
_API_V1_PROXY_PREFIX = "/api/dispatch/api/v1"


# ops.example.com RETIRED 2026-08-02 per operator direction --
# dispatch-runner is now fully functional with proper gating (demo-mode
# password gate on dispatch-runner.example.com, Tailscale-only
# HTTPS for the live instance at port 8001), so the old always-open public
# mirror of the LIVE instance is no longer needed and is a real exposure
# (it's how the unauthenticated /api/rss/user-feeds write routes were
# reachable from the public internet). The clean fix is removing the
# Cloudflare Tunnel Public Hostname route entirely, but this tunnel is
# dashboard-managed (remotely managed) -- confirmed 2026-08-02, the local
# ~/.cloudflared/config.yml `ingress` list is NOT the live routing source
# for it (edited it, restarted cloudflared, "Updated to new configuration"
# log still showed ops. plus three hostnames -- mcp/cockpit/dav -- that
# were never even in the local file, proving the daemon pulls ingress from
# Cloudflare's control plane, not this file). Killing the tunnel-level
# route needs the CF Zero Trust dashboard (Networks > Tunnels > dispatch >
# Public Hostname tab) or a scoped API token, neither available to this
# session -- flagged to the operator as a follow-up, not blocking this fix.
# In the meantime this is a real, complete kill at the one layer this
# session CAN deploy: any request whose Host header is exactly
# ops.example.com is hard-rejected before touching any route,
# the same "surface doesn't exist" 404 philosophy as the admin-path check
# below, so even while the DNS/tunnel route still technically exists,
# nothing behind it is reachable through that hostname anymore.
_RETIRED_HOSTNAMES = {"ops.example.com"}


@app.middleware("http")
async def tailscale_gate(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0].strip().lower()
    if host in _RETIRED_HOSTNAMES:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    path = request.url.path
    is_admin_proxy_path = path.startswith(_ADMIN_PROXY_PREFIX) or path.startswith("/admin")
    is_v1_mutation = (
        path.startswith(_API_V1_PROXY_PREFIX)
        and request.method != "GET"
    )
    if (is_admin_proxy_path or is_v1_mutation) and not _is_trusted(request):
        # 404, not 403 -- per operator direction the point isn't "you're
        # denied," it's "this surface doesn't exist to you." A 403 would
        # confirm an admin path is there to probe against; a 404 doesn't.
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return await call_next(request)


@app.get("/api/whoami")
async def whoami(request: Request):
    """
    Tells the frontend whether THIS request arrived over a trusted
    (Tailscale/LAN) origin, so it can decide whether to render the ADMIN
    nav tab and the Settings token field at all. Added 2026-07-21 so Ops
    (public hostname) never even shows admin UI exists, rather than
    showing it disabled/broken -- matches the tailscale_gate enforcement
    above, using the same _is_trusted check for consistency.
    """
    return {"tailnet": _is_trusted(request)}


# ── Demo-mode login gate ------------------------------------------------------
# Only reachable/meaningful when DEMO_MODE=true (runner-demo instance, port
# 8005). Added 2026-08-01 so the public dispatch-runner.example.com
# hostname actually enforces the password-gated access the operator asked for --
# see the hard gate in proxy_dispatch() below for the enforcement half of
# this; these two routes are just login + status.

@app.post("/api/demo/login")
async def demo_login(request: Request):
    """Public: exchanges a password for a session. Proxies the actual
    check to demo_api's /api/v1/demo/login (that service holds the real
    profiles DB, this one doesn't) and, on success, sets an HttpOnly
    cookie so the raw token never touches frontend JS -- the frontend
    only ever sees the boolean/label/window/speed fields below, never
    the token itself."""
    if not DEMO_MODE:
        raise HTTPException(404, "Not found")
    body = await request.json()
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{DISPATCH_BASE}/api/v1/demo/login",
                              json={"password": body.get("password", "")},
                              timeout=10)
    except httpx.HTTPError:
        raise HTTPException(502, "Demo backend unreachable")
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Invalid password")
    data = r.json()
    resp = JSONResponse({
        "ok": True,
        "label": data["label"],
        "window_days": data["window_days"],
        "speed": data["speed"],
    })
    resp.set_cookie(
        DEMO_SESSION_COOKIE, data["session"],
        max_age=data.get("expires_in_seconds", 8 * 3600),
        httponly=True, secure=True, samesite="lax", path="/",
    )
    return resp


@app.get("/api/demo/status")
async def demo_status(request: Request):
    """Tells the frontend whether this is a demo-mode instance, whether
    THIS request is already authenticated -- either a valid session
    cookie, or arriving from a trusted/Tailscale origin, which per
    operator direction never needs to log in at all -- and the active
    profile's label/window/speed for a small "viewing: X demo" banner."""
    if not DEMO_MODE:
        return {"demo_mode": False, "authenticated": True}
    if _is_trusted(request):
        return {"demo_mode": True, "authenticated": True, "trusted_origin": True}
    payload = _verify_demo_session(request.cookies.get(DEMO_SESSION_COOKIE))
    if payload:
        return {
            "demo_mode": True, "authenticated": True, "trusted_origin": False,
            "label": payload.get("label"),
            "window_days": payload.get("window_days"),
            "speed": payload.get("speed"),
        }
    return {"demo_mode": True, "authenticated": False}


# ── Health -------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "dispatch-runner", "version": "1.1"}

# ── ADS-B proxy -------------------------------------------------------------

@app.get("/api/adsb/local")
async def adsb_local():
    """Proxy to local UltraFeeder tar1090 aircraft.json."""
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(ULTRAFEEDER_URL, timeout=5)
            r.raise_for_status()
            return {**r.json(), "source": "local"}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"UltraFeeder unavailable: {e}")

@app.get("/api/adsb/live")
async def adsb_live(
    lat: float = Query(DEFAULT_LAT),
    lon: float = Query(DEFAULT_LON),
    dist: int  = Query(DEFAULT_DIST),
):
    """Proxy to airplanes.live -- full area window regardless of antenna range."""
    url = f"{AIRPLANES_LIVE}/aircraft/lat/{lat}/lon/{lon}/dist/{dist}"
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(url, timeout=12,
                            headers={"User-Agent": "corporatetraveldc/1.0"})
            r.raise_for_status()
            return {**r.json(), "source": "airplanes.live"}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"airplanes.live unavailable: {e}")

# ── Signal proxy helpers -----------------------------------------------------

def _acarsdrama_headers() -> dict:
    return {
        "Authorization": f"Bearer {ACARSDRAMA_TOKEN}",
        "X-API-Key": ACARSDRAMA_TOKEN,
        "Accept": "application/json",
    }

def _airframes_headers() -> dict:
    return {
        "X-Airframes-Token": AIRFRAMES_TOKEN,
        "Accept": "application/json",
    }

def _normalize_jumpseat_msg(m: dict) -> dict:
    """
    Normalize a Jumpseat API message to the canonical frontend schema.

    Jumpseat field → canonical field:
      timestamp (ISO8601)  → timestamp (preserved) + time (HH:MM:SS UTC)
      registration         → callsign (primary identifier)
      flightNumber         → flight (stripped if literal "null")
      cleanedText          → text
      directionLabel       → direction
      stationLocation      → location
      aircraft.icaoType    → icao_type
      aircraft.friendlyType→ aircraft_type
      isAutomated          → automated
    """
    ts_raw = m.get("timestamp", "")
    time_str = ""
    try:
        dt = datetime.datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        time_str = dt.strftime("%H:%M:%S")
    except Exception:
        time_str = ts_raw[11:19] if len(ts_raw) >= 19 else ts_raw

    reg    = m.get("registration") or ""
    flight = m.get("flightNumber") or ""
    if flight in ("null", "None", "N/A"):
        flight = ""
    callsign = reg or flight or "?"

    aircraft = m.get("aircraft") or {}
    return {
        "id":           m.get("id"),
        "timestamp":    ts_raw,
        "time":         time_str,
        "callsign":     callsign,
        "flight":       flight,
        "registration": reg,
        "protocol":     m.get("protocol", ""),
        "direction":    m.get("directionLabel") or m.get("direction", ""),
        "location":     m.get("stationLocation", ""),
        "icao_type":    aircraft.get("icaoType") or "",
        "aircraft_type": aircraft.get("friendlyType") or "",
        "text":         (m.get("cleanedText") or "").strip(),
        "automated":    bool(m.get("isAutomated")),
    }

async def _acarshub_messages(msg_type: str, since: int) -> list:
    """
    acarshub serves its UI via HTTP but message data via WebSocket only.
    Its /api/* paths return HTML (the SPA shell), not JSON.
    Raises immediately so callers fall through to acarsdrama Jumpseat.
    """
    raise NotImplementedError("acarshub is WebSocket-only; use Jumpseat fallback")

async def _acarsdrama_messages(protocol_filter: str, since: int,
                                lat: float, lon: float, dist: int) -> list:
    """
    Fetch from acarsdrama Jumpseat API (primary external fallback).
    Endpoint: GET /v1/messages/search
    Confirmed params (2026-06-09 test):
      source=messages  -- only valid source value; returns all protocol types
      lat, lon, radius -- geographic filter (nm)
      limit            -- max results per page
    Response: {"items": [{..., "protocol": "VDLM2"|"ACARS"|"HFDL", ...}]}
    We filter client-side by protocol field since source= has no type filter.
    protocol_filter: "VDLM2" | "ACARS" | "HFDL" | "" (empty = all types)
    Multiple external sources are additive (feeder rate benefits), not
    purely sequential fallback -- both acarsdrama and airframes may run.
    """
    if not ACARSDRAMA_TOKEN:
        return []
    url = f"{ACARSDRAMA_BASE}/messages/search"
    params = {"source": "messages", "lat": lat, "lon": lon,
              "radius": dist, "limit": 200}
    if since:
        params["since"] = since
    async with httpx.AsyncClient() as c:
        r = await c.get(url, params=params,
                        headers=_acarsdrama_headers(), timeout=10)
        r.raise_for_status()
        data = r.json()
        items = data.get("items") or []
        if protocol_filter:
            pf = protocol_filter.upper()
            items = [m for m in items
                     if (m.get("protocol") or "").upper() == pf]
        return [_normalize_jumpseat_msg(m) for m in items]


# Fixed 2026-08-03: the old implementation assumed protocol-specific
# endpoints (/vdl2, /acars, /hfdl) with lat/lon/radius params. Confirmed
# live that path 404s -- it never existed. airframes.io's real and only
# message-listing route is the unified /v1/messages (docs.airframes.io/api),
# which has NO geographic filter at all; protocol is selected client-side
# via each message's sourceType field. Verified live with a real token:
# a last-hour pull returned sourceType values vdl / acars / hfdl /
# aero-acars / iridium-acars -- HFDL genuinely present (17 of 100 in one
# sample), confirming this is a real usable fallback for HFDL, not a dead
# end. ACARS-family satellite variants (aero-acars, iridium-acars) are
# folded into the "acars" bucket since they're the same message class over
# a different link, matching what the ACARS tab already represents.
_AIRFRAMES_SOURCE_TYPES = {
    "vdl2":  ("vdl",),
    "acars": ("acars", "aero-acars", "iridium-acars"),
    "hfdl":  ("hfdl",),
}


def _normalize_airframes_msg(m: dict) -> dict:
    """
    Normalize an airframes.io /v1/messages record to the canonical
    frontend schema (same shape _normalize_jumpseat_msg produces).

    airframes.io field   → canonical field:
      timestamp/createdAt  → timestamp (preserved) + time (HH:MM:SS UTC)
      tail                  → registration / callsign
      flightNumber          → flight
      sourceType            → protocol (upper-cased)
      linkDirection         → direction
      station.ident         → location
      airframe.icaoType     → icao_type
      airframe.description  → aircraft_type
      text / data           → text
    """
    ts_raw = m.get("timestamp") or m.get("createdAt") or ""
    time_str = ""
    try:
        dt = datetime.datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        time_str = dt.strftime("%H:%M:%S")
    except Exception:
        time_str = ts_raw[11:19] if len(ts_raw) >= 19 else ts_raw

    airframe = m.get("airframe") or {}
    station  = m.get("station") or {}

    reg    = m.get("tail") or airframe.get("tail") or ""
    flight = m.get("flightNumber") or ""
    if flight in ("null", "None", "N/A"):
        flight = ""
    callsign = reg or flight or "?"

    return {
        "id":            m.get("id"),
        "timestamp":     ts_raw,
        "time":          time_str,
        "callsign":      callsign,
        "flight":        flight,
        "registration":  reg,
        "protocol":      (m.get("sourceType") or "").upper(),
        "direction":     m.get("linkDirection") or "",
        "location":      station.get("ident") or "",
        "icao_type":     airframe.get("icaoType") or "",
        "aircraft_type": airframe.get("description") or "",
        "text":          (m.get("text") or m.get("data") or "").strip(),
        "automated":     False,
    }


async def _airframes_messages(endpoint: str, since: int,
                               lat: float, lon: float, dist: int) -> list:
    """
    Fetch from airframes.io's real API (secondary external fallback).
    Only called when both local acarshub and acarsdrama are unavailable.

    lat/lon/dist are accepted only to keep this function's call signature
    unchanged for its three callers (vdl2/acars/hfdl_messages) -- the real
    airframes.io /v1/messages endpoint has no geographic filter, so these
    are not sent and have no effect on this fallback tier. If that becomes
    a problem (this fallback pulling non-DC-area traffic), the fix is
    client-side filtering on message.station.latitude/longitude, not a
    request param -- airframes.io simply doesn't expose one.
    """
    if not AIRFRAMES_TOKEN:
        return []
    source_types = _AIRFRAMES_SOURCE_TYPES.get(endpoint, (endpoint,))
    url = f"{AIRFRAMES_BASE.rstrip('/')}/messages"
    params = {"limit": 100}  # exclude_errors=true triggers a live 500 on airframes.io -- confirmed 2026-08-03, omit it
    if since:
        params["since"] = datetime.datetime.fromtimestamp(
            since, tz=datetime.timezone.utc).isoformat()
    else:
        params["timeframe"] = "last-hour"
    async with httpx.AsyncClient() as c:
        r = await c.get(url, params=params,
                        headers=_airframes_headers(), timeout=15)
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else (data.get("data") or data.get("messages") or [])
        items = [m for m in items if (m.get("sourceType") or "") in source_types]
        return [_normalize_airframes_msg(m) for m in items]

# ── Demo-mode signal sanitization --------------------------------------------
# VDL2/ACARS/HFDL are NOT proxied through demo_api.py's replay archive --
# these three routes always call the real live decoder/aggregator chain
# above, in both live and demo mode. Real registrations, callsigns, flight
# numbers, and receiver station location have to be stripped here, at the
# only point they ever pass through this process, before a public demo
# visitor's browser ever sees them.
#
# The real->fake mapping is process-local and in-memory only (never
# written to disk, never sent to a client, reset on every container
# restart) -- it exists purely so the same real aircraft renders as the
# same fake identity across multiple messages within one running demo
# session, not as a way to "remember" real tails anywhere persistent.
_signal_tail_map:   dict[str, str] = {}
_signal_flight_map: dict[str, str] = {}
_SANITIZE_SALT = os.getenv("DEMO_SANITIZE_SALT") or DEMO_SESSION_SECRET or "ctdc-demo-signal-sanitize"
_FAKE_CARRIERS = ["DEM", "XYZ", "QDC"]  # deliberately fictional-looking, not real IATA/ICAO codes


def _synthetic_tail(real: str) -> str:
    key = (real or "").strip().upper()
    if not key:
        return real
    if key not in _signal_tail_map:
        h = hashlib.sha256((_SANITIZE_SALT + "TAIL" + key).encode()).hexdigest()
        _signal_tail_map[key] = "N" + str(int(h[:5], 16) % 90000 + 10000)
    return _signal_tail_map[key]


def _synthetic_flight(real: str) -> str:
    key = (real or "").strip().upper()
    if not key:
        return real
    if key not in _signal_flight_map:
        h = hashlib.sha256((_SANITIZE_SALT + "FLT" + key).encode()).hexdigest()
        carrier = _FAKE_CARRIERS[int(h[:2], 16) % len(_FAKE_CARRIERS)]
        num = int(h[2:6], 16) % 9000 + 100
        _signal_flight_map[key] = f"{carrier}{num}"
    return _signal_flight_map[key]


def _sanitize_signal_message(m: dict) -> dict:
    """Replace registration/callsign/flight with a per-process-consistent
    synthetic identity, scrub the same real substrings out of free-text
    `text` (decoded ACARS/VDL2 payloads frequently repeat the tail/flight
    inline), and generalize the receiving station location so a public
    demo visitor can't infer the operator's physical feeder location."""
    m = dict(m)
    real_reg      = (m.get("registration") or "").strip()
    real_flight   = (m.get("flight") or "").strip()
    real_callsign = (m.get("callsign") or "").strip()

    fake_reg    = _synthetic_tail(real_reg) if real_reg else real_reg
    fake_flight = _synthetic_flight(real_flight) if real_flight else real_flight

    if real_callsign and real_callsign == real_reg:
        fake_callsign = fake_reg
    elif real_callsign and real_callsign == real_flight:
        fake_callsign = fake_flight
    elif real_callsign and real_callsign != "?":
        fake_callsign = _synthetic_tail(real_callsign)
    else:
        fake_callsign = real_callsign

    text = m.get("text") or ""
    if real_reg:
        text = re.sub(re.escape(real_reg), fake_reg, text, flags=re.IGNORECASE)
    if real_flight:
        text = re.sub(re.escape(real_flight), fake_flight, text, flags=re.IGNORECASE)

    m["registration"] = fake_reg
    m["flight"]        = fake_flight
    m["callsign"]      = fake_callsign
    m["text"]          = text
    if m.get("location"):
        m["location"] = "DC-METRO"
    return m


def _sanitize_signal_payload(payload: dict) -> dict:
    payload = dict(payload)
    payload["messages"] = [_sanitize_signal_message(m) for m in (payload.get("messages") or [])]
    return payload


def _should_sanitize_signals(request: Request) -> bool:
    return DEMO_MODE and not _is_trusted(request)


# ── VDL2 endpoint -----------------------------------------------------------

@app.get("/api/vdl2/messages")
async def vdl2_messages(
    request: Request,
    since: int   = Query(0),
    lat:   float = Query(DEFAULT_LAT),
    lon:   float = Query(DEFAULT_LON),
    dist:  int   = Query(DEFAULT_DIST),
):
    """VDL2 messages. Local acarshub first; falls back to airframes.io."""
    sanitize = _should_sanitize_signals(request)
    try:
        msgs = await _acarshub_messages("vdl2", since)
        result = {"source": "local", "messages": msgs, "count": len(msgs)}
        return _sanitize_signal_payload(result) if sanitize else result
    except Exception as e:
        log.debug("VDL2 local unavailable: %s -- trying acarsdrama", e)
    try:
        msgs = await _acarsdrama_messages("VDLM2", since, lat, lon, dist)
        result = {"source": "acarsdrama.com", "messages": msgs, "count": len(msgs)}
        return _sanitize_signal_payload(result) if sanitize else result
    except Exception as e:
        log.debug("VDL2 acarsdrama unavailable: %s -- trying airframes.io", e)
    try:
        msgs = await _airframes_messages("vdl2", since, lat, lon, dist)
        result = {"source": "airframes.io", "messages": msgs, "count": len(msgs)}
        return _sanitize_signal_payload(result) if sanitize else result
    except Exception as e:
        log.warning("VDL2 all sources unavailable: %s", e)
    return {"source": "none", "messages": [], "count": 0}

# ── ACARS endpoint ----------------------------------------------------------

@app.get("/api/acars/messages")
async def acars_messages(
    request: Request,
    since: int   = Query(0),
    lat:   float = Query(DEFAULT_LAT),
    lon:   float = Query(DEFAULT_LON),
    dist:  int   = Query(DEFAULT_DIST),
):
    """ACARS messages. Local acarshub first; falls back to airframes.io."""
    sanitize = _should_sanitize_signals(request)
    try:
        msgs = await _acarshub_messages("acars", since)
        result = {"source": "local", "messages": msgs, "count": len(msgs)}
        return _sanitize_signal_payload(result) if sanitize else result
    except Exception as e:
        log.debug("ACARS local unavailable: %s -- trying acarsdrama", e)
    try:
        msgs = await _acarsdrama_messages("ACARS", since, lat, lon, dist)
        result = {"source": "acarsdrama.com", "messages": msgs, "count": len(msgs)}
        return _sanitize_signal_payload(result) if sanitize else result
    except Exception as e:
        log.debug("ACARS acarsdrama unavailable: %s -- trying airframes.io", e)
    try:
        msgs = await _airframes_messages("acars", since, lat, lon, dist)
        result = {"source": "airframes.io", "messages": msgs, "count": len(msgs)}
        return _sanitize_signal_payload(result) if sanitize else result
    except Exception as e:
        log.warning("ACARS all sources unavailable: %s", e)
    return {"source": "none", "messages": [], "count": 0}

# ── HFDL endpoint -----------------------------------------------------------

@app.get("/api/hfdl/messages")
async def hfdl_messages(
    request: Request,
    since: int   = Query(0),
    lat:   float = Query(DEFAULT_LAT),
    lon:   float = Query(DEFAULT_LON),
    dist:  int   = Query(DEFAULT_DIST),
):
    """
    HFDL messages. Local acarshub first, then acarsdrama, then airframes.io.

    Fixed 2026-08-03: unlike VDL2/ACARS, acarsdrama's Jumpseat API is
    confirmed (live-tested, 200-message sample) to carry ZERO HFDL traffic
    at any tier -- it's a VHF-only aggregator, not a config/subscription
    problem on our end. That call still returns 200 with an empty list, not
    an error, so the old exception-only fallback logic returned "0 results,
    source: acarsdrama.com" and stopped -- airframes.io (which DOES carry
    real HFDL, confirmed live) was never reached. HFDL specifically now
    falls through to airframes.io on an EMPTY acarsdrama result too, not
    just a hard failure. VDL2/ACARS are left on exception-only fallback
    deliberately -- acarsdrama does carry real traffic for those, so a
    legitimately quiet window shouldn't burn an extra airframes.io call.
    """
    sanitize = _should_sanitize_signals(request)
    try:
        msgs = await _acarshub_messages("hfdl", since)
        result = {"source": "local", "messages": msgs, "count": len(msgs)}
        return _sanitize_signal_payload(result) if sanitize else result
    except Exception as e:
        log.debug("HFDL local unavailable: %s -- trying acarsdrama", e)
    try:
        msgs = await _acarsdrama_messages("HFDL", since, lat, lon, dist)
        if msgs:
            result = {"source": "acarsdrama.com", "messages": msgs, "count": len(msgs)}
            return _sanitize_signal_payload(result) if sanitize else result
        log.debug("HFDL acarsdrama returned 0 messages (Jumpseat carries no HFDL) -- trying airframes.io")
    except Exception as e:
        log.debug("HFDL acarsdrama unavailable: %s -- trying airframes.io", e)
    try:
        msgs = await _airframes_messages("hfdl", since, lat, lon, dist)
        result = {"source": "airframes.io", "messages": msgs, "count": len(msgs)}
        return _sanitize_signal_payload(result) if sanitize else result
    except Exception as e:
        log.warning("HFDL all sources unavailable: %s", e)
    hw = "hardware_pending" if not ACARSDRAMA_TOKEN and not AIRFRAMES_TOKEN else "unavailable"
    return {"source": "none", "messages": [], "count": 0, "detail": hw}

# ── AIS helpers -------------------------------------------------------------

def _bbox(lat: float, lon: float, dist_nm: int) -> dict:
    """Approximate bounding box for a radius in nautical miles (~1 nm ≈ 1/60 deg lat)."""
    dlat = dist_nm / 60.0
    dlon = dlat / max(math.cos(math.radians(lat)), 0.01)
    return {
        "MINLAT": round(lat - dlat, 4), "MAXLAT": round(lat + dlat, 4),
        "MINLON": round(lon - dlon, 4), "MAXLON": round(lon + dlon, 4),
    }

def _norm_vessel(v: dict, source: str) -> dict:
    """Normalise vessel dict from any source to a common schema."""
    if source == "local":
        return {
            "mmsi":       str(v.get("mmsi", "")),
            "name":       v.get("name", "").strip(),
            "lat":        v.get("lat") or v.get("latitude"),
            "lon":        v.get("lon") or v.get("longitude"),
            "sog":        v.get("speed") or v.get("sog"),
            "cog":        v.get("course") or v.get("cog"),
            "hdg":        v.get("heading"),
            "nav_status": v.get("navstat") or v.get("status"),
            "ship_type":  v.get("shiptype") or v.get("ship_type"),
        }
    if source == "aishub.net":
        return {
            "mmsi":       str(v.get("MMSI", "")),
            "name":       v.get("NAME", "").strip(),
            "lat":        v.get("LATITUDE"),
            "lon":        v.get("LONGITUDE"),
            "sog":        v.get("SOG"),
            "cog":        v.get("COG"),
            "hdg":        v.get("HEADING"),
            "nav_status": v.get("NAVSTAT"),
            "ship_type":  v.get("TYPE"),
        }
    return v

def _norm_vessel_kpler(node: dict) -> dict:
    """
    Normalise a Kpler Maritime 2.0 GraphQL vessel node (nested staticData /
    lastPositionUpdate objects) to the same flat schema used by the local
    and AISHub sources.
    """
    static = node.get("staticData") or {}
    pos    = node.get("lastPositionUpdate") or {}
    return {
        "mmsi":       str(static.get("mmsi", "")),
        "name":       (static.get("name") or "").strip(),
        "lat":        pos.get("latitude"),
        "lon":        pos.get("longitude"),
        "sog":        pos.get("speed"),
        "cog":        pos.get("course"),
        "hdg":        pos.get("heading"),
        "nav_status": pos.get("navigationalStatus"),
        "ship_type":  static.get("shipType"),
    }


async def _fetch_kpler_vessels(lat: float, lon: float, dist: int) -> dict:
    """
    Kpler Maritime 2.0 GraphQL API -- successor to the MarineTraffic REST
    Vessels API (services.marinetraffic.com/api/getVessels/v:8/...), which
    Kpler fully discontinued platform-wide in 2025 after acquiring Spire
    Maritime/MarineTraffic. The old endpoint 404s unconditionally now --
    this is not a per-key permission issue, the whole legacy API family is
    gone. See docs/DATA_SOURCES.md for the migration story and how to
    request a Maritime 2.0 token (a distinct credential from the
    MarineTraffic embed widget key -- AIS_MARINETRAFFIC_KEY above -- which
    is a separate, still-functioning product).

    Raises on any HTTP or GraphQL-level error; caller is responsible for
    catching and falling through to the next tier, same as every other
    source in this fallback chain.
    """
    bbox = _bbox(lat, lon, dist)
    polygon = (
        f"[[{bbox['MINLON']}, {bbox['MINLAT']}], "
        f"[{bbox['MAXLON']}, {bbox['MINLAT']}], "
        f"[{bbox['MAXLON']}, {bbox['MAXLAT']}], "
        f"[{bbox['MINLON']}, {bbox['MAXLAT']}], "
        f"[{bbox['MINLON']}, {bbox['MINLAT']}]]"
    )
    query = (
        "query VesselsInArea {"
        "  vessels(areaOfInterest: {polygon: {type: \"Polygon\", coordinates: ["
        + polygon +
        "]}}, first: 200) {"
        "    nodes {"
        "      staticData { name mmsi shipType }"
        "      lastPositionUpdate { latitude longitude heading speed course navigationalStatus }"
        "    }"
        "  }"
        "}"
    )
    async with httpx.AsyncClient() as c:
        r = await c.post(
            KPLER_GRAPHQL_URL,
            headers={
                "Authorization": f"Bearer {KPLER_MARITIME_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"query": query},
            timeout=12,
        )
        r.raise_for_status()
        payload = r.json()
        if payload.get("errors"):
            raise RuntimeError(payload["errors"][0].get("message", "Kpler GraphQL error"))
        nodes = ((payload.get("data") or {}).get("vessels") or {}).get("nodes") or []
        vessels = [_norm_vessel_kpler(n) for n in nodes]
        return {"source": "marinetraffic.com", "vessels": vessels, "count": len(vessels)}

# ── AIS endpoint ------------------------------------------------------------

@app.get("/api/ais/vessels")
async def ais_vessels(
    lat:  float = Query(DEFAULT_LAT),
    lon:  float = Query(DEFAULT_LON),
    dist: int   = Query(DEFAULT_DIST),
):
    """
    AIS vessel positions.
    Fallback chain: local AIS-catcher -> AISHub -> none.

    UPDATED 2026-07-21 per operator direction: dropped the Kpler Maritime
    2.0 tier entirely ("mt is being a douchebag" -- MarineTraffic/Kpler
    access is a sales-gated enterprise product and the operator has no
    working credential for it, only an embed-widget key that was never
    going to work here; see docs/DATA_SOURCES.md). AISHub is free,
    reciprocal, and already working once AIS_AISHUB_ID is registered, so
    it's the only real fallback tier now. _fetch_kpler_vessels is left
    defined but unused below rather than deleted, in case Kpler access is
    ever actually obtained later -- not wired into this fallback chain.
    Returns normalised vessel objects regardless of source.
    """
    # 1 -- Local AIS-catcher (hardware)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{AIS_CATCHER_URL}/vessels.json", timeout=5)
            r.raise_for_status()
            data = r.json()
            raw = data.get("vessels") or (data if isinstance(data, list) else [])
            vessels = [_norm_vessel(v, "local") for v in raw if isinstance(v, dict)]
            return {"source": "local", "vessels": vessels, "count": len(vessels)}
    except Exception as e:
        log.debug("AIS local unavailable: %s", e)

    # 2 -- AISHub (free data-sharing cooperative, requires registration)
    if AIS_AISHUB_ID:
        try:
            bbox = _bbox(lat, lon, min(dist, 120))  # AISHub free tier: smaller window
            params = {
                "username": AIS_AISHUB_ID, "format": "1",
                "output": "json", "compress": "0",
                "latmin": bbox["MINLAT"], "latmax": bbox["MAXLAT"],
                "lonmin": bbox["MINLON"], "lonmax": bbox["MAXLON"],
            }
            async with httpx.AsyncClient() as c:
                r = await c.get(AIS_AISHUB_BASE, params=params, timeout=12,
                                headers={"User-Agent": "corporatetraveldc/1.0"})
                r.raise_for_status()
                data = r.json()
                # AISHub response: [{metadata}, vessel1, vessel2, ...]
                raw = [v for v in data if isinstance(v, dict) and "MMSI" in v]
                vessels = [_norm_vessel(v, "aishub.net") for v in raw]
                return {"source": "aishub.net", "vessels": vessels, "count": len(vessels)}
        except Exception as e:
            log.warning("AIS AISHub unavailable: %s", e)

    return {"source": "none", "vessels": [], "count": 0, "detail": "no_source_configured"}

# ── Dispatch AI chat (Local/Cloud LLM) -------------------------------------
# These local inference endpoints are also exposed as portable MCP tools.
# MCP server: https://github.com/CorporateTravelDC/corporatetravel-dispatch-mcp
# Use with Claude Code, Cline, Cursor, Zed, Windsurf, or Open WebUI via mcpo.

class AskRequest(BaseModel):
    message: str
    history: list[dict] = []
    model:   Optional[str] = None  # operator override; None → use OLLAMA_CHAT_MODEL


# Topic keyword patterns — used by the local resolver to classify queries.
# A query matching any of these patterns gets a structured local answer
# built directly from dispatch feed data, no LLM required.
_TOPIC_RX: dict[str, re.Pattern] = {
    "cps":     re.compile(r'\b(cps|go[\s\-]?no[\s\-]?go|hems)\b', re.I),
    "weather": re.compile(r'\b(weather|metar|wind|ceiling|vis|wx)\b', re.I),
    "tfr":     re.compile(r'\b(tfr|flight[\s\-]?restrict|potus|marine[\s\-]?one|vip[\s\-]?air)\b', re.I),
    "amtrak":  re.compile(r'\b(amtrak|train|was\b|union[\s\-]?sta)\b', re.I),
    "notam":   re.compile(r'\bnotams?\b', re.I),
    "alerts":  re.compile(r'\b(alert|warning|advisory|nws)\b', re.I),
    "feeds":   re.compile(r'\b(feed|health|nominal|degrad|error)\b', re.I),
    "brief":   re.compile(r'\b(brief|summary|status|situation|sitrep)\b', re.I),
}


async def _build_context_rich() -> dict[str, Any]:
    """
    Fetch all dispatch feed data in parallel. Returns a structured dict keyed
    by topic. Never raises — missing feeds produce no entry (caller handles).
    This is the canonical data source; LLMs get a stringified view of this,
    but the raw dict is available for local resolution without LLM involvement.
    """
    endpoints = {
        "cps":     "api/v1/cps",
        "weather": "api/v1/weather",
        "tfr":     "api/v1/tfr",
        "feeds":   "api/v1/feeds",
        "alerts":  "api/v1/alerts",
        "notam":   "api/v1/notams",
        "amtrak":  "api/v1/amtrak",
        "brief":   "api/v1/brief",
    }
    ctx: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=5) as c:
        tasks = {
            topic: asyncio.create_task(c.get(f"{DISPATCH_BASE}/{path}"))
            for topic, path in endpoints.items()
        }
        for topic, task in tasks.items():
            try:
                r = await task
                if r.status_code == 200:
                    ctx[topic] = r.json()
            except Exception:
                pass
    return ctx


def _context_to_str(ctx: dict[str, Any]) -> str:
    """Flatten rich context dict to a string block for LLM injection."""
    parts: list[str] = []

    if "cps" in ctx:
        cps = ctx["cps"]
        parts.append(
            f"CPS: {cps.get('score','?')} / {cps.get('label','?')} — "
            f"{cps.get('narrative','')}"
        )

    if "weather" in ctx:
        stations = ctx["weather"].get("stations") or {}
        wx_lines = [
            f"  {icao}: {(d.get('raw_text') or '').strip()}"
            for icao, d in list(stations.items())[:6]
            if d.get("raw_text")
        ]
        if wx_lines:
            parts.append("WEATHER (METAR):\n" + "\n".join(wx_lines))

    if "tfr" in ctx:
        tfrs = ctx["tfr"]
        if isinstance(tfrs, list) and tfrs:
            vip = [t for t in tfrs if t.get("is_vip")]
            ids = ", ".join(t.get("notam_id") or t.get("id") or "?" for t in tfrs[:6])
            parts.append(f"TFRS: {len(tfrs)} active ({len(vip)} VIP/POTUS) — {ids}")
        else:
            parts.append("TFRS: none active")

    if "feeds" in ctx:
        feed_list = ctx["feeds"].get("feeds") if isinstance(ctx["feeds"], dict) else ctx["feeds"]
        if isinstance(feed_list, list):
            errors = [
                f.get("feed_name") for f in feed_list
                if f.get("error") and "pending" not in str(f.get("error", ""))
            ]
            stale  = [
                f.get("feed_name") for f in feed_list
                if (f.get("age_seconds") or 0) > 900 and not f.get("error")
            ]
            if errors:
                parts.append(f"FEED ERRORS: {', '.join(filter(None, errors))}")
            elif stale:
                parts.append(f"FEEDS STALE: {', '.join(filter(None, stale))}")
            else:
                parts.append("FEEDS: nominal")

    if "alerts" in ctx:
        alerts = ctx["alerts"]
        if isinstance(alerts, list) and alerts:
            headlines = "; ".join(
                a.get("headline") or a.get("event") or "?" for a in alerts[:3]
            )
            parts.append(f"NWS ALERTS ({len(alerts)}): {headlines}")

    if "amtrak" in ctx:
        amtrak = ctx["amtrak"]
        summary = amtrak.get("summary", "")
        if summary:
            parts.append(f"AMTRAK/WAS: {summary}")

    if "notam" in ctx:
        notams = ctx["notam"]
        if isinstance(notams, list) and notams:
            parts.append(f"NOTAMS: {len(notams)} active")

    return "\n".join(parts)


def _local_answer(query: str, ctx: dict[str, Any]) -> str | None:
    """
    Try to answer the query purely from local dispatch data.
    Returns a formatted string if the query matches a known topic,
    or None if LLM synthesis is needed for a general/free-form query.

    This is Tier 0 — it runs before any LLM is consulted. If the query
    can be answered here, it is — instantly, from local data, with no
    network dependency beyond the dispatch spine itself.
    """
    matched = [t for t, rx in _TOPIC_RX.items() if rx.search(query)]
    if not matched:
        return None  # general query — pass to LLM tier

    parts: list[str] = []

    if "cps" in matched and "cps" in ctx:
        cps = ctx["cps"]
        score = cps.get("score", "?")
        label = cps.get("label", "")
        narr  = cps.get("narrative", "")
        parts.append(f"CPS: {score}{(' — ' + label) if label else ''}")
        if narr:
            parts.append(narr)
        factors = cps.get("factors") or {}
        if factors:
            parts.append("Factors: " + ", ".join(f"{k}={v}" for k, v in factors.items()))

    if "weather" in matched and "weather" in ctx:
        stations = ctx["weather"].get("stations") or {}
        wx_lines = [
            f"  {icao}: {(d.get('raw_text') or '').strip()}"
            for icao, d in stations.items()
            if d.get("raw_text")
        ]
        if wx_lines:
            parts.append("METAR:\n" + "\n".join(wx_lines))
        else:
            parts.append("METAR: no data")

    if "tfr" in matched and "tfr" in ctx:
        tfrs = ctx["tfr"]
        if isinstance(tfrs, list):
            if tfrs:
                vip = [t for t in tfrs if t.get("is_vip")]
                parts.append(f"TFRs active: {len(tfrs)} ({len(vip)} VIP/POTUS)")
                for t in tfrs[:8]:
                    nid  = t.get("notam_id") or t.get("id") or "?"
                    area = (t.get("area") or "")[:60]
                    eff  = t.get("effective") or ""
                    parts.append(f"  {nid}: {area}{(' eff '+eff) if eff else ''}".strip())
            else:
                parts.append("TFRs: none active")

    if "amtrak" in matched and "amtrak" in ctx:
        amtrak = ctx["amtrak"]
        summary = amtrak.get("summary", "")
        parts.append(f"Amtrak/WAS: {summary}" if summary else "Amtrak: no data")

    if "notam" in matched and "notam" in ctx:
        notams = ctx["notam"]
        if isinstance(notams, list) and notams:
            parts.append(f"NOTAMs: {len(notams)} active")
            for n in notams[:5]:
                nid  = n.get("notam_id") or n.get("id") or "?"
                text = (n.get("text") or n.get("message") or "")[:100]
                parts.append(f"  {nid}: {text}")
        else:
            parts.append("NOTAMs: none active")

    if "alerts" in matched and "alerts" in ctx:
        alerts = ctx["alerts"]
        if isinstance(alerts, list) and alerts:
            parts.append(f"NWS Alerts: {len(alerts)}")
            for a in alerts[:5]:
                headline = a.get("headline") or a.get("event") or "?"
                parts.append(f"  {headline}")
        else:
            parts.append("NWS Alerts: none active")

    if "feeds" in matched and "feeds" in ctx:
        feed_list = (
            ctx["feeds"].get("feeds")
            if isinstance(ctx["feeds"], dict)
            else ctx["feeds"]
        )
        if isinstance(feed_list, list):
            errors = [
                f.get("feed_name") for f in feed_list
                if f.get("error") and "pending" not in str(f.get("error", ""))
            ]
            stale = [
                f.get("feed_name") for f in feed_list
                if (f.get("age_seconds") or 0) > 900 and not f.get("error")
            ]
            nominal = [
                f.get("feed_name") for f in feed_list
                if not f.get("error") and (f.get("age_seconds") or 0) <= 900
            ]
            if errors:
                parts.append(f"Feed errors: {', '.join(filter(None, errors))}")
            if stale:
                parts.append(f"Feeds stale: {', '.join(filter(None, stale))}")
            if not errors and not stale:
                parts.append(f"Feeds nominal: {len(nominal)} active")

    if "brief" in matched and "brief" in ctx:
        brief = ctx["brief"]
        if isinstance(brief, dict):
            text = brief.get("text") or brief.get("summary") or brief.get("brief") or ""
            if text:
                parts.append(f"Brief:\n{text[:600]}")
            else:
                # flatten whatever keys exist
                parts.append("Brief: " + json.dumps(brief, default=str)[:300])
        elif isinstance(brief, str):
            parts.append(f"Brief:\n{brief[:600]}")

    return "\n".join(parts) if parts else None


async def _llm_stream(system: str, messages: list[dict], model: str | None = None):
    """
    Async generator — yields raw SSE data lines from Ollama.
    model: explicit override; falls back to OLLAMA_CHAT_MODEL if None.
    Yields {"type":"no_llm"} if Ollama is not configured.
    Yields {"type":"model_info","model":"..."} as first event so the frontend
    can display which model serviced the request.
    """
    # ── Open WebUI proxy → Ollama (local LLM, no external deps) ─────────────
    endpoint = _chat_endpoint()
    if endpoint:
        resolved_model = model or OLLAMA_CHAT_MODEL

        # ── Pre-flight: check if Ollama is saturated (single-slot, Pi 5) ──────
        # /api/ps returns currently loaded/running models. If a model shows
        # size_vram > 0 it means inference is active and the slot is occupied.
        # We probe the direct Ollama URL regardless of OPENWEBUI_URL routing.
        ollama_direct = OLLAMA_BASE_URL.rstrip("/") if OLLAMA_BASE_URL else "http://host.containers.internal:11434"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3, read=5, write=3, pool=3)) as probe:
                ps = await probe.get(f"{ollama_direct}/api/ps")
                if ps.status_code == 200:
                    ps_data = ps.json()
                    models_running = ps_data.get("models", [])
                    if models_running and any(m.get("size_vram", 0) > 0 for m in models_running):
                        log.info("Ollama slot occupied — yielding model_busy, falling back to local data")
                        yield f"data: {json.dumps({'type': 'no_llm'})}\n\n"
                        return
        except Exception as probe_err:
            log.debug("Ollama /api/ps probe failed (%s) — proceeding", probe_err)

        try:
            payload = {
                "model":    resolved_model,
                "stream":   True,
                "messages": [{"role": "system", "content": system}] + messages,
            }
            yield f"data: {json.dumps({'type': 'model_info', 'model': resolved_model})}\n\n"
            # read timeout of 40s: long enough for the first token on a free Pi 5
            # 12B model (typically 10-30s), short enough to fail fast when queued.
            llm_timeout = httpx.Timeout(connect=10, read=40, write=10, pool=10)
            async with httpx.AsyncClient(timeout=llm_timeout) as c:
                async with c.stream(
                    "POST",
                    endpoint,
                    json=payload,
                    headers=_chat_headers(),
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            obj     = json.loads(line)
                            content = obj.get("message", {}).get("content", "")
                            if content:
                                yield f"data: {json.dumps({'type': 'text', 'text': content})}\n\n"
                            if obj.get("done"):
                                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                                return
                        except Exception:
                            pass
            return
        except Exception as e:
            log.warning("LLM backend unavailable (%s) — no LLM fallback", e)

    # ── No LLM configured or backend unreachable ──────────────────────────────
    yield f"data: {json.dumps({'type': 'no_llm'})}\n\n"


@app.post("/api/ask")
async def ask_dispatch(req: AskRequest):
    """
    Local-first dispatch chat. Resolution order:

    1. Fetch all dispatch feed data (always — unconditional, no LLM needed).
    2. Run local resolver: if the query matches a known topic (CPS, weather,
       TFRs, Amtrak, NOTAMs, alerts, feeds, brief), build a structured
       answer directly from the data — zero LLM, zero external dependency.
    3. If an LLM is configured (Ollama preferred, Anthropic fallback),
       synthesize a natural-language response using the full data as context.
    4. If no LLM is available, stream the local structured answer directly.

    The LLM is a synthesis layer, not a gatekeeper. Every query returns
    something useful as long as the dispatch spine is reachable.

    SSE events: {"type":"text","text":"..."} | {"type":"done"} | {"type":"error","detail":"..."}
    """
    has_llm = bool(OPENWEBUI_URL or OLLAMA_BASE_URL)

    # ── Operator model override: "/model <name> <rest-of-message>" ────────────
    # Stripping before history insertion so the model directive doesn't
    # pollute future context (the assistant still sees the real query).
    raw_message  = req.message.strip()
    model_override: str | None = req.model  # from JSON body takes priority
    _MODEL_PREFIX = re.compile(r'^/model\s+(\S+)\s*(.*)', re.S)
    _mx = _MODEL_PREFIX.match(raw_message)
    if _mx:
        model_override = _mx.group(1).strip()
        raw_message    = _mx.group(2).strip() or raw_message  # keep full msg if no remainder
    effective_model = model_override or OLLAMA_CHAT_MODEL

    # Load history from persistent DB (last 40 turns, chronological).
    messages = await asyncio.to_thread(_chat_load_history, 40)
    messages.append({"role": "user", "content": raw_message})

    async def stream_response():
        # Emit keep-alive comment immediately — flushes Cloudflare/proxy buffer
        # before context fetch (up to 5s with parallel dispatch requests).
        yield ": keep-alive\n\n"

        ctx     = await _build_context_rich()
        local   = _local_answer(raw_message, ctx)
        ctx_str = _context_to_str(ctx)
        now     = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        system_prompt = (
            "You are the dispatch AI assistant for [operator LLC], LLC — a boutique executive services firm: "
            "automotive detailing, brand strategy, executive chauffeur transportation, and IT security. "
            "Operator: the operator (N0CALL Extra, ARES NoVA District XX, Skywarn LXXXX).\n\n"
            "All live operational data below comes from a local dispatch spine running on-premises. "
            "It is the authoritative source. Do not speculate beyond it.\n\n"
            f"CURRENT DISPATCH STATE ({now}):\n"
            f"{ctx_str if ctx_str else 'No data available from dispatch spine.'}\n\n"
            "OPERATOR CONTEXT:\n"
            "- Location: [operator county], [state] / KDCA (15 min)\n"
            "- Airspace: DC FRZ/SFRA, P-56A/B, concentric rings 50/100/150/250nm\n"
            "- Ground ops: [chauffeur partner] + Uber Black\n"
            "- Emergency: ARES NoVA, CERT County+County, Skywarn LWX (LXXXX), GMRS WRXXXXX\n"
            "- Dispatch spine: Pi 5, Tailscale (example.ts.net)\n\n"
            "Respond in plain text. No markdown. Brief and tactical unless elaboration requested. "
            "For HEMS go/no-go, always cite CPS score and narrative."
        )

        # Accumulate assistant text for DB persistence.
        assistant_parts: list[str] = []

        def _capture(chunk: str) -> None:
            """Extract text payload from SSE chunk and append to assistant_parts."""
            try:
                payload = json.loads(chunk.split("data: ", 1)[1].rstrip())
                if payload.get("type") == "text":
                    assistant_parts.append(payload["text"])
            except Exception:
                pass

        if not has_llm:
            # No LLM available — serve local data directly, always useful.
            answer = local or ctx_str or "Dispatch spine unreachable — no data available."
            assistant_parts.append(answer)
            yield f"data: {json.dumps({'type': 'text', 'text': answer})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            await asyncio.to_thread(_chat_save_exchange, raw_message, answer)
            return

        # LLM available — synthesize with full context injected.
        # If LLM is unavailable mid-stream, fall through to local answer.
        got_any = False
        async for chunk in _llm_stream(system_prompt, messages, model=effective_model):
            if '"type": "no_llm"' in chunk or '"type":"no_llm"' in chunk:
                # Backend reported no LLM — fall back to local data
                answer = local or ctx_str or "Dispatch spine unreachable."
                assistant_parts.append(answer)
                yield f"data: {json.dumps({'type': 'text', 'text': answer})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                await asyncio.to_thread(
                    _chat_save_exchange, raw_message, "".join(assistant_parts)
                )
                return
            got_any = True
            _capture(chunk)
            yield chunk

        if not got_any:
            answer = local or ctx_str or "No response from any backend."
            assistant_parts.append(answer)
            yield f"data: {json.dumps({'type': 'text', 'text': answer})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        # Persist the full exchange (using stripped raw_message, not the /model directive).
        full_response = "".join(assistant_parts)
        if full_response:
            await asyncio.to_thread(_chat_save_exchange, raw_message, full_response)

    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":      "no-cache",
            "Connection":         "keep-alive",
            "X-Accel-Buffering":  "no",
            "X-Dispatch-Model":   effective_model,
        },
    )


# ── Dispatch chat history endpoints ─────────────────────────────────────────

@app.get("/api/chat/history")
async def chat_history(limit: int = 80):
    """Return persisted chat history (newest `limit` messages, chronological)."""
    msgs = await asyncio.to_thread(_chat_load_history, limit)
    return {"messages": msgs, "count": len(msgs)}


@app.delete("/api/chat/history")
async def chat_history_clear():
    """Erase all chat history from the persistent DB."""
    def _clear():
        with sqlite3.connect(CHAT_DB_PATH) as c:
            c.execute("DELETE FROM chat_messages")
    await asyncio.to_thread(_clear)
    return {"status": "cleared"}


# ── Dispatch API transparent proxy -----------------------------------------
# Paths (relative to /api/dispatch/) that require Tier 1 (cert) auth.
# The runner holds a cert-tier service token and injects it for these.
# Add new Tier-1 endpoints here as they are promoted.
_TIER1_PATHS: frozenset[str] = frozenset({
    "api/v1/tfr-enriched",
    "api/v1/radio",
    "api/v1/cui/status",
    # Added 2026-07-21: dispatch-web gates GET /api/v1/watchlist itself at
    # tier1 (CERT/Tailscale) regardless of what this runner's own
    # tailscale_gate middleware allows through. Per operator direction Ops
    # (public hostname) should see the REAL watchlist read-only -- the
    # runner's own middleware already blocks any WRITE to this path from a
    # non-trusted origin, so injecting the service token here only widens
    # the READ, matching the same pattern already used for tfr-enriched/
    # radio/cui-status above. This is what actually makes "Ops sees
    # everything, view-only" true; without it every GET from the public
    # hostname 403s at dispatch-web before the runner's own logic matters.
    "api/v1/watchlist",
    "api/v1/watchlist/history",
})
# NOTE: watchlist (VIP flight/person tracking) deliberately excluded from
# _TIER1_PATHS -- this proxy will not inject its own service token for it.
# See _WATCHLIST_PATHS / _is_tailnet_request() below for the actual gate.

# UPDATED 2026-07-21 per operator direction: dropped the paired-auth
# placeholder gate entirely. Ops (public hostname) now sees the REAL
# watchlist on every GET -- it's a view-only dashboard, not a "hide the
# data" concern anymore. What's actually gated is MUTATION (add/remove),
# enforced by tailscale_gate's is_v1_mutation check above, which blocks
# any non-GET /api/v1/* request that doesn't arrive from a trusted
# (Tailscale/LAN) origin, before it ever reaches this proxy.

@app.api_route("/api/dispatch/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy_dispatch(path: str, request: Request):
    """Transparent proxy to dispatch web API on port 8000.

    Auth injection: for Tier-1-gated endpoints (e.g. api/v1/tfr-enriched) the
    runner injects its own service token when the browser hasn't supplied one.
    This lets the frontend call enriched endpoints without holding a token itself.
    The service token (RUNNER_ENRICHED_TOKEN, tier=cert) is stored server-side in
    dispatch-secrets.env and never exposed to the browser.

    Admin paths and non-GET api/v1/* mutations are rejected before they
    even reach this function -- see tailscale_gate middleware above.

    Demo-mode gate (added 2026-08-01): when DEMO_MODE=true and this
    request did NOT arrive over a trusted (Tailscale/LAN) origin, a valid
    demo session cookie is required or the request is rejected outright --
    this is what makes the public dispatch-runner.example.com
    hostname actually password-gated rather than falling through to
    demo_api's own lenient default-window/speed behavior. Trusted-origin
    requests (the operator, over Tailscale) are completely unaffected -- no
    cookie, no login, exactly today's open behavior.
    """
    demo_session_token: str | None = None
    if DEMO_MODE and not _is_trusted(request):
        demo_session_token = request.cookies.get(DEMO_SESSION_COOKIE)
        if not _verify_demo_session(demo_session_token):
            raise HTTPException(401, "Demo login required")

    auth = request.headers.get("Authorization")

    url = f"{DISPATCH_BASE}/{path}"
    headers = {}

    # Client-supplied token takes priority (admin console, debug flows).
    if auth:
        headers["Authorization"] = auth
    elif RUNNER_ENRICHED_TOKEN and path in _TIER1_PATHS:
        # Server-side token injection for known Tier-1 endpoints.
        headers["Authorization"] = f"Bearer {RUNNER_ENRICHED_TOKEN}"
    params = dict(request.query_params)
    if demo_session_token:
        # Verified above -- overrides any window/speed the browser itself
        # tried to pass, demo_api's own resolver prioritizes session over
        # raw query params the same way.
        params["session"] = demo_session_token

    try:
        async with httpx.AsyncClient() as c:
            if request.method == "GET":
                r = await c.get(url, params=params,
                                headers=headers, timeout=10)
            else:
                body = await request.body()
                r = await c.request(
                    request.method, url, content=body,
                    headers={**headers,
                             "Content-Type": request.headers.get(
                                 "Content-Type", "application/json")},
                    timeout=10)
        ct = r.headers.get("content-type", "")
        if "text/plain" in ct:
            return PlainTextResponse(r.text, status_code=r.status_code)
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Dispatch unavailable: {e}")

# ── User layer config (cross-device persistence) ----------------------------
# Stored at /var/lib/corporatetraveldc/runner-layer-config.json.
# Gated behind CF Access for the domain — no additional token auth needed
# for a single-operator deployment. The frontend sends Bearer token only when
# it has one; this endpoint works either way.

_CONFIG_PATH = os.path.join(os.getenv("STATE_DIR", "/var/lib/corporatetraveldc"),
                            "runner-layer-config.json")
CHAT_DB_PATH = os.path.join(os.getenv("STATE_DIR", "/var/lib/corporatetraveldc"),
                            "dispatch-chat.db")


# ── Persistent dispatch chat DB ─────────────────────────────────────────────

def _chat_db_init() -> None:
    """Create chat_messages table on first run."""
    with sqlite3.connect(CHAT_DB_PATH) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            role     TEXT    NOT NULL CHECK(role IN ('user','assistant')),
            content  TEXT    NOT NULL,
            ts       REAL    NOT NULL
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_chat_ts ON chat_messages(ts)")


def _chat_load_history(limit: int = 40) -> list[dict]:
    """Return last `limit` messages in chronological order."""
    with sqlite3.connect(CHAT_DB_PATH) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT role, content FROM chat_messages ORDER BY ts DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def _chat_save_exchange(user_msg: str, assistant_msg: str) -> None:
    """Persist one user/assistant exchange atomically."""
    now = time.time()
    with sqlite3.connect(CHAT_DB_PATH) as c:
        c.execute(
            "INSERT INTO chat_messages (role, content, ts) VALUES (?, ?, ?)",
            ("user", user_msg, now - 0.001),
        )
        c.execute(
            "INSERT INTO chat_messages (role, content, ts) VALUES (?, ?, ?)",
            ("assistant", assistant_msg, now),
        )


@app.on_event("startup")
async def startup_event():
    await asyncio.to_thread(_chat_db_init)

@app.get("/api/v1/frontend-config")
async def frontend_config():
    """Expose non-secret runtime config values to the React frontend."""
    return {"mt_widget_key": AIS_MT_WIDGET_KEY}


@app.get("/api/v1/config")
async def get_user_config():
    """Return persisted layer config, or empty object if none saved yet."""
    try:
        with open(_CONFIG_PATH) as f:
            return JSONResponse(json.load(f))
    except FileNotFoundError:
        return JSONResponse({})
    except Exception as e:
        log.warning("runner: config read failed: %s", e)
        return JSONResponse({})

@app.put("/api/v1/config")
async def put_user_config(request: Request):
    """Persist layer config from request body (JSON)."""
    try:
        body = await request.json()
        os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
        with open(_CONFIG_PATH, "w") as f:
            json.dump(body, f)
        return JSONResponse({"ok": True})
    except Exception as e:
        log.warning("runner: config write failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── SSE state stream --------------------------------------------------------

async def _fetch_state() -> dict:
    result: dict = {}
    async with httpx.AsyncClient() as c:
        for key, path in [("cps", "api/v1/cps"), ("feeds", "api/v1/feeds"),
                          ("tfr", "api/v1/tfr"), ("healthz", "healthz")]:
            try:
                r = await c.get(f"{DISPATCH_BASE}/{path}", timeout=5)
                result[key] = r.json() if r.status_code == 200 else None
            except Exception:
                result[key] = None
    if isinstance(result.get("tfr"), list):
        tfrs = result["tfr"]
        result["tfr_count"] = len(tfrs)
        result["vip_count"] = sum(1 for t in tfrs if t.get("is_vip"))
        del result["tfr"]
    return result

@app.get("/api/stream")
async def sse_stream(request: Request):
    """Server-Sent Events: CPS + feed health + TFR summary every 30s."""
    async def generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                state = await _fetch_state()
                yield f"data: {json.dumps({'type': 'state', **state})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"
            await asyncio.sleep(SSE_INTERVAL_SEC)
    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})

# ── Demo-mode synthetic alert feed + webhook fan-out --------------------------
# Added 2026-08-03. Two things this closes/adds:
#
# (a) /api/ntfy/stream below was a raw, unfiltered proxy straight to the
#     real ntfy broker -- same leak class as the AIS and ACARS/VDL2/HFDL
#     routes found earlier: any public demo visitor saw genuine live
#     TFR/CPS/flight-watchlist/weather alerts, completely unsanitized.
#     Fixed by swapping in a synthetic, clearly-fictional alert stream for
#     any DEMO_MODE + untrusted-origin request, using the same N##### /
#     DEM-XYZ-QDC synthetic-identity convention already used for
#     ACARS/VDL2/HFDL sanitization, so nothing here is even superficially
#     confusable with real operational content.
#
# (b) the operator's ask: showcase that this same alert bus can fan out via
#     webhook into a reservation system (LimoAnywhere) and call-center
#     platforms (3CX, RingCentral) -- all three genuinely support inbound
#     webhooks per their own docs, so this is a real, representative
#     integration pattern, not a fabricated one. Demo-only for now (no
#     real account to point at yet): each synthetic alert also fires a
#     best-effort fan-out to three in-process mock receivers, and a small
#     ring buffer of what each received is exposed for the frontend to
#     display -- so a visitor can watch one alert land in the feed AND on
#     all three "external" mock endpoints in real time. Nothing here
#     touches the real pusher/alerting stack; it only runs inside the
#     demo-mode gate, entirely separate from live operations.

_DEMO_SYNTHETIC_ALERTS = [
    {"topic": "tfr-alert",         "title": "Sample TFR",       "message": "Example: temporary flight restriction posted near KDCA -- sample data for demonstration, not a real advisory."},
    {"topic": "wx-alerts",         "title": "Sample WX",        "message": "Example: METAR at KIAD shifting toward MVFR, ceiling trending down -- sample data for demonstration."},
    {"topic": "flight-alerts",     "title": "N40217 [a1b2c3]",  "message": "N40217 [a1b2c3] DEM4821 -- sample position 38.9N 77.0W FL280 CRUISE | OOOI: watching (demo data)"},
    {"topic": "flight-alerts",     "title": "N75530 -- BAGGAGE","message": "N75530 XYZ1190: sample bags ~14:35 (approach, +20min) -- KDCA (demo data)"},
    {"topic": "cps",               "title": "Sample CPS",       "message": "Example: Critical Predictability State GO -- ceiling/vis/wind/precip/airspace/GDP nominal (sample data)."},
    {"topic": "hot-alerts",        "title": "Sample Hot Alert", "message": "Example: elevated ground-route impact flagged for a DC-metro corridor -- sample data for demonstration."},
    {"topic": "train-alerts",      "title": "Sample Rail",      "message": "Example: Acela 2151 running +8min into WAS -- sample data for demonstration."},
    {"topic": "dispatch",          "title": "Sample Health",    "message": "Example: all feeds nominal, watchdog last run clean -- sample data for demonstration."},
    {"topic": "dispatch-debriefs", "title": "Sample Debrief",   "message": "DEM4821 N40217 a1b2c3 | 38.900N 77.000W 28000ft 410kts CRUISE | sq:2200 (demo data)"},
    {"topic": "ops-brief",         "title": "Sample Brief",     "message": "OPS BRIEF (sample) -- conditions nominal across tracked feeds. This is illustrative demo content, not a real operational brief."},
]

_demo_webhook_log: dict[str, list[dict]] = {"limoanywhere": [], "threecx": [], "ringcentral": []}
_DEMO_WEBHOOK_LOG_MAX = 20


async def _demo_fanout_alert(alert: dict) -> None:
    """Best-effort fan-out of one synthetic demo alert to the three mock
    receivers, mirroring what a real outbound webhook delivery to
    LimoAnywhere/3CX/RingCentral would carry. In-process only (mock
    receivers are just this same app's own in-memory log, see below) --
    no real network call leaves the box, matching the rest of the demo's
    privacy boundary, while still exercising the exact same payload shape
    a real webhook integration would use."""
    payload = {
        "event": "dispatch_alert",
        "source": "[operator LLC] Dispatch",
        "topic": alert["topic"],
        "title": alert["title"],
        "message": alert["message"],
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    for name in ("limoanywhere", "threecx", "ringcentral"):
        buf = _demo_webhook_log[name]
        buf.insert(0, payload)
        del buf[_DEMO_WEBHOOK_LOG_MAX:]


@app.get("/api/demo/webhook-log")
async def demo_webhook_log(request: Request):
    """Recent payloads each mock receiver has gotten -- backs the
    Integrations panel on the Feed tab. Demo-mode only; 404s on the live
    instance and for trusted/Tailscale visitors on the demo instance (no
    reason to show marketing fixtures to the operator)."""
    if not DEMO_MODE or _is_trusted(request):
        raise HTTPException(404, "Not found")
    return {
        "limoanywhere": _demo_webhook_log["limoanywhere"],
        "threecx":      _demo_webhook_log["threecx"],
        "ringcentral":  _demo_webhook_log["ringcentral"],
    }


async def _synthetic_ntfy_stream(request: Request):
    """Demo-mode replacement for the real ntfy proxy. Cycles through
    _DEMO_SYNTHETIC_ALERTS on a loop with light randomized pacing (25-45s
    between messages, shuffled order per loop) so the feed feels alive
    without ever being a 1:1 timed reveal of the same fixed script twice
    in a row. Each emitted alert also drives the webhook fan-out above."""
    yield "data: {\"type\":\"heartbeat\"}\n\n"
    rotation = list(_DEMO_SYNTHETIC_ALERTS)
    idx = 0
    counter = 0
    while True:
        if await request.is_disconnected():
            break
        if idx % len(rotation) == 0:
            random.shuffle(rotation)
        alert = rotation[idx % len(rotation)]
        idx += 1
        counter += 1
        await _demo_fanout_alert(alert)
        msg = {
            "id": f"demo-{counter}",
            "time": int(time.time()),
            "event": "message",
            "topic": alert["topic"],
            "title": alert["title"],
            "message": alert["message"],
        }
        yield f"data: {json.dumps(msg)}\n\n"
        await asyncio.sleep(random.uniform(25, 45))


# ── ntfy feed proxy ─────────────────────────────────────────────────────────
# Streams ntfy SSE through the runner so the frontend avoids CORS/auth issues.
# Known topics: tfr-alert, hot-alerts, flight-alerts, cps, ops-health,
#               train-alerts, wx-alerts, osint-alerts, dispatch,
#               dispatch-debriefs, ops-brief

@app.get("/api/ntfy/stream")
async def ntfy_stream(request: Request, topics: str = "dispatch,wx-alerts,flight-alerts,tfr-alert,cps,ops-health,train-alerts,ops-brief"):
    """Proxy ntfy SSE feed to the frontend.

    ?topics=comma,separated,topic,names
    Streams ntfy JSON events as SSE data lines.

    DEMO_MODE + untrusted origin: real ntfy is never touched at all --
    _synthetic_ntfy_stream() above takes over entirely. See its docstring
    and the block above it for why (this route used to leak real live
    alerts to public demo visitors, same bug class as AIS/ACARS/VDL2/HFDL).
    """
    if _should_sanitize_signals(request):
        return StreamingResponse(
            _synthetic_ntfy_stream(request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                     "X-Accel-Buffering": "no"},
        )
    topic_str = topics.replace(" ", "")
    # ?since=1h: replay the last hour of messages on connect so the feed
    # populates immediately rather than waiting for the next live event.
    ntfy_sse_url = f"{NTFY_URL.rstrip('/')}/{topic_str}/sse?since=1h"

    headers = {}
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"

    async def generator():
        # Send a heartbeat immediately so the client knows the stream is alive
        yield "data: {\"type\":\"heartbeat\"}\n\n"
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", ntfy_sse_url, headers=headers) as r:
                    if r.status_code != 200:
                        yield f"data: {{\"type\":\"error\",\"detail\":\"ntfy returned {r.status_code}\"}}\n\n"
                        return
                    async for line in r.aiter_lines():
                        if await request.is_disconnected():
                            break
                        if line.startswith("data:"):
                            yield f"{line}\n\n"
                        elif line == "":
                            pass  # blank separator — skip
        except Exception as e:
            yield f"data: {{\"type\":\"error\",\"detail\":\"{str(e)[:120]}\"}}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


# ── RSS intel proxy ──────────────────────────────────────────────────────────
# Fetches and parses RSS/Atom feeds server-side to avoid CORS.
# Returns normalised JSON items.

# 2026-07-28: catalog + user-feed persistence moved to shared/rss_catalog.py
# so second_brain_rss (a separate poller-container skill) can churn on this
# exact same pool instead of a disconnected list -- operator request. All
# names below are re-imported under their original names so nothing else
# in this file needs to change.
from shared.rss_catalog import _RSS_CATALOG

_NS = {
    "atom":    "http://www.w3.org/2005/Atom",
    "media":   "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def _normalize_pubdate(raw: str) -> str:
    """Normalize RSS pubDate (RFC 2822) or Atom date to ISO 8601 for reliable sort.

    RFC 2822 ("Fri, 12 Jun 2026 20:38:34 +0000") sorts alphabetically by day-name
    which is wrong. Convert to ISO 8601 so string reverse-sort gives newest-first.
    ISO/Atom dates ("2026-06-12T...") are returned unchanged — they already sort fine.
    """
    if not raw:
        return ""
    # Already ISO 8601 (Atom feeds) — starts with digit year
    if raw[:4].isdigit():
        return raw
    # RFC 2822 — parse via email.utils
    try:
        from email.utils import parsedate_to_datetime as _p2dt
        return _p2dt(raw).isoformat()
    except Exception:
        return raw  # leave malformed dates as-is; they'll sort last


def _parse_rss(xml_bytes: bytes, source_name: str, per_feed_limit: int = 100) -> list[dict]:
    """Parse RSS/Atom XML into a list of normalised item dicts.

    Returns up to per_feed_limit items, sorted newest-first. This prevents a
    single podcast archive from drowning out news feeds in merged responses.
    """
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return items

    # Atom feed
    ns = root.tag.split("}")[0].lstrip("{") if "}" in root.tag else ""
    if "atom" in ns or root.tag.endswith("}feed"):
        for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
            title  = entry.findtext("{http://www.w3.org/2005/Atom}title", "").strip()
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link   = (link_el.get("href") or "") if link_el is not None else ""
            summ   = entry.findtext("{http://www.w3.org/2005/Atom}summary", "").strip()
            pub    = (entry.findtext("{http://www.w3.org/2005/Atom}published") or
                      entry.findtext("{http://www.w3.org/2005/Atom}updated") or "")
            items.append({"title": title, "link": link, "summary": summ[:280],
                          "published": _normalize_pubdate(pub), "source": source_name})
        items.sort(key=lambda x: x.get("published", ""), reverse=True)
        return items[:per_feed_limit]

    # RSS 2.0
    for item in root.findall(".//item"):
        title   = (item.findtext("title") or "").strip()
        link    = (item.findtext("link") or "").strip()
        desc    = (item.findtext("description") or "").strip()
        desc    = re.sub(r"<[^>]+>", "", desc)[:280]
        pub     = (item.findtext("pubDate") or item.findtext("dc:date") or "").strip()
        # Podcast enclosure — capture audio/video URL if present
        enc_el  = item.find("enclosure")
        audio_url = ""
        if enc_el is not None:
            enc_type = enc_el.get("type", "")
            if enc_type.startswith(("audio/", "video/")):
                audio_url = enc_el.get("url", "")
        entry = {"title": title, "link": link, "summary": desc,
                 "published": _normalize_pubdate(pub), "source": source_name}
        if audio_url:
            entry["audio_url"] = audio_url
        items.append(entry)

    items.sort(key=lambda x: x.get("published", ""), reverse=True)
    return items[:per_feed_limit]


# Simple in-memory RSS cache: cache_key → (timestamp, items)
_rss_cache: dict[str, tuple[float, list[dict]]] = {}
_RSS_TTL = 900  # 15 minutes

# ── User-defined feed registry (backend-persisted) ───────────────────────────
# 2026-07-28: implementation moved to shared/rss_catalog.py (see note at
# _RSS_CATALOG above) -- re-imported under the original names.
import json as _json
import uuid as _uuid

from shared.rss_catalog import USER_FEEDS_PATH as _USER_FEEDS_PATH
from shared.rss_catalog import load_user_feeds as _load_user_feeds
from shared.rss_catalog import save_user_feeds as _save_user_feeds
from shared.rss_catalog import load_user_categories as _load_user_categories
from shared.rss_catalog import save_user_categories as _save_user_categories
from shared.rss_catalog import list_all_categories as _list_all_categories
from shared.rss_catalog import find_existing_category as _find_existing_category
from shared.rss_catalog import visible_to as _visible_to
from shared.feed_resolve import resolve_source as _resolve_source

_user_feeds_lock = __import__("asyncio").Lock()
_user_categories_lock = __import__("asyncio").Lock()

_ANON_IDENTITY = {"tier": "tier0", "user_label": None, "department": None, "token_prefix": None}
_identity_cache: dict[str, tuple[float, dict]] = {}
_IDENTITY_CACHE_TTL = 60


async def _resolve_operator_identity(request: Request) -> dict:
    """
    Added 2026-08-02 for the department/multi-operator RSS visibility
    model. The runner never touches the shared DB directly (see this
    file's README note on that boundary), so identity resolution is a
    proxied call to web/main.py's /api/v1/whoami-token rather than a local
    token lookup -- that endpoint wraps auth.auth.resolve_identity(),
    which itself never raises, so this never raises either; a missing,
    invalid, or unreachable-backend token all resolve to _ANON_IDENTITY.
    Cached per raw Authorization header value for _IDENTITY_CACHE_TTL
    seconds so a burst of RSS calls (rss_feed() fetches N feeds per
    category request) doesn't add a network round-trip per call.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header:
        return _ANON_IDENTITY
    now = time.time()
    cached = _identity_cache.get(auth_header)
    if cached and cached[0] > now:
        return cached[1]
    identity = _ANON_IDENTITY
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{DISPATCH_BASE}/api/v1/whoami-token",
                             headers={"Authorization": auth_header})
        if r.status_code == 200:
            identity = r.json()
    except httpx.HTTPError as e:
        log.warning("_resolve_operator_identity: whoami-token call failed: %s", e)
    _identity_cache[auth_header] = (now + _IDENTITY_CACHE_TTL, identity)
    return identity


async def _fetch_one_rss(client: "httpx.AsyncClient", feed: dict, cache_prefix: str) -> list[dict]:
    """Fetch and cache a single RSS feed dict {name, url}. Returns normalised items."""
    cache_key = f"{cache_prefix}:{feed['url']}"
    now = time.time()
    cached = _rss_cache.get(cache_key)
    if cached and (now - cached[0]) < _RSS_TTL:
        return cached[1]
    try:
        r = await client.get(feed["url"],
                             headers={"User-Agent": "corporatetraveldc-dispatch/1.0"})
        if r.status_code == 200:
            parsed = _parse_rss(r.content, feed["name"])
            _rss_cache[cache_key] = (now, parsed)
            return parsed
        else:
            log.warning("rss: %s returned %d", feed["url"], r.status_code)
    except Exception as e:
        log.warning("rss: fetch %s failed: %s", feed["url"], e)
    return []


@app.get("/api/rss")
async def rss_feed(request: Request, category: str = "corporate_intel", limit: int = 200):
    """Fetch and return normalised RSS items for a category.

    Merges catalog feeds + any user-defined feeds assigned to this category
    that are visible to the caller (company-scope always included;
    department/personal-scope only if the caller's resolved identity
    matches -- see shared.rss_catalog.visible_to(), added 2026-08-02).
    ?category=corporate_intel|marketing_intel|travel_trends|dc_area|aviation|<user-category-id>|__custom__
    ?limit=N  — max items to return (default 200, max 500). Each feed is capped at
               100 items before merging to prevent podcast archives from swamping news.
    """
    identity = await _resolve_operator_identity(request)
    valid_cats = {c["id"] for c in _list_all_categories(identity)} | {"__custom__"}
    if category not in valid_cats:
        raise HTTPException(status_code=400,
                            detail=f"Unknown category. Valid: {sorted(valid_cats)}")
    limit = min(max(limit, 1), 500)

    catalog_feeds = _RSS_CATALOG.get(category, [])
    user_feeds    = [f for f in _load_user_feeds()
                     if f.get("category") == category and _visible_to(f, identity)]
    all_feeds     = catalog_feeds + user_feeds

    all_items: list[dict] = []
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        for feed in all_feeds:
            items = await _fetch_one_rss(client, feed, category)
            # 2026-08-07: provenance passthrough for entity_tracking's
            # backlink-discovered feeds (see common/entity_tracking.py) --
            # operator directive: auto-discovered-source content must be
            # visibly marked, not blended in with hand-curated feeds.
            if feed.get("discovered"):
                for it in items:
                    it["discovered"] = True
            all_items.extend(items)

    all_items.sort(key=lambda x: x.get("published", ""), reverse=True)
    return {"category": category, "count": len(all_items), "items": all_items[:limit]}


# ── RSS catalog listing ──────────────────────────────────────────────────────
@app.get("/api/rss/categories")
async def rss_categories(request: Request):
    """Return available RSS categories.

    UPDATED 2026-08-02: previously returned only the hardcoded built-in
    catalog (name -> feed list); now also includes user-created categories
    (Add Category) visible to the caller. Response shape changed to a list
    of {id, label, scope, department?, owner?, builtin?} objects rather
    than a bare {cat: [feeds...]} dict, since categories are now real
    entities with their own identity, not just dict keys -- the frontend's
    CATALOG_CATEGORIES hardcoded array is retired in favor of calling this.
    Built-in catalog feed contents are still available via the existing
    /api/rss?category=X endpoint, unchanged.
    """
    identity = await _resolve_operator_identity(request)
    return {"categories": _list_all_categories(identity)}


@app.post("/api/rss/categories")
async def rss_categories_add(request: Request, body: dict):
    """
    Create a user-defined category. Added 2026-08-02 (Add Category,
    parallel to Add Feed).

    Body: {label: str, scope: "company"|"department"|"personal" (default
    "company"), department: str (required if scope="department")}.
    Requires a resolved identity (Bearer token) -- anonymous callers may
    not create categories, matching the write-gating added to Add Feed in
    this same change (closes the previously-unauthenticated write surface,
    see the ops.example.com retirement note near tailscale_gate
    above).
    """
    identity = await _resolve_operator_identity(request)
    if identity["tier"] == "tier0":
        raise HTTPException(status_code=401, detail="A valid token is required to create a category.")

    label      = (body.get("label") or "").strip()
    scope      = (body.get("scope") or "company").strip()
    department = (body.get("department") or "").strip() or None

    if not label:
        raise HTTPException(status_code=400, detail="label is required.")
    if scope not in ("company", "department", "personal"):
        raise HTTPException(status_code=400, detail='scope must be one of: company, department, personal')
    if scope == "department" and not department:
        raise HTTPException(status_code=400, detail="department is required when scope='department'.")
    if scope == "department" and identity.get("department") and department != identity["department"]:
        # Not a hard security boundary (there's no cross-department secret
        # here), just guards against fat-fingering a typo'd department name
        # nobody else's token will ever match -- the caller can still name
        # any department their own token doesn't have, on purpose, for the
        # "set this up ahead of a department that doesn't have tokens yet"
        # case, but not silently.
        log.info("rss_categories_add: caller department=%s creating category for different department=%s",
                  identity.get("department"), department)

    async with _user_categories_lock:
        categories = _load_user_categories()
        # Alias-aware duplicate guard (2026-08-07): refuse if this names a
        # category -- or the same concept (shorthand/jargon/STT-variant, e.g.
        # "AAM"/"eVTOL"/"EV tolls" all == advanced_air_mobility) -- that already
        # exists, built-in or user-created. Prevents the empty-duplicate bug.
        dup = _find_existing_category(label, categories)
        if dup:
            raise HTTPException(status_code=409, detail=(
                f"A category for this already exists: '{dup['label']}' "
                f"({dup['source']} category, id={dup['id']}). Add feeds to it "
                f"instead of creating a duplicate."))
        cat_id = f"user_{_uuid.uuid4().hex[:12]}"
        entry = {
            "id":         cat_id,
            "label":      label,
            "scope":      scope,
            "department": department if scope == "department" else None,
            "owner":      identity.get("token_prefix") if scope == "personal" else None,
            "created_by": identity.get("user_label"),
        }
        categories.append(entry)
        _save_user_categories(categories)

    log.info("user_rss_categories: added %s (scope=%s) by %s", label, scope, identity.get("user_label"))
    return {"category": entry}


# ── Custom RSS proxy (validate + preview a feed URL) ─────────────────────────
@app.get("/api/rss/custom")
async def rss_custom(url: str, name: str = "Custom"):
    """Fetch and proxy an arbitrary RSS/Atom feed URL server-side (avoids CORS).

    Used for preview/validation before saving. Returns same shape as /api/rss.
    """
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            items = await _fetch_one_rss(client, {"name": name, "url": url}, "custom")
        if not items:
            raise HTTPException(status_code=422,
                                detail="URL returned 200 but no RSS/Atom items could be parsed. "
                                       "Check that it is a valid RSS or Atom feed.")
        return {"count": len(items), "items": items}
    except HTTPException:
        raise
    except Exception as e:
        log.warning("rss/custom: fetch %s failed: %s", url, e)
        raise HTTPException(status_code=502, detail=f"Could not fetch feed: {e}")


@app.post("/api/rss/resolve-source")
async def rss_resolve_source(request: Request, body: dict):
    """Resolve an arbitrary source URL (YouTube channel, Rumble channel, or a
    blog homepage) into an actual RSS/Atom feed URL, so Add Feed can accept
    "paste the channel/blog link" instead of requiring a pre-built feed URL.

    Added 2026-08-03 per operator request for direct YouTube/Rumble/blog
    support. See shared/feed_resolve.py's module docstring for the three
    resolution strategies and the known Rumble/Cloudflare limitation found
    during live testing (RSS-Bridge's RumbleBridge is wired up correctly,
    but Rumble's own bot protection currently blocks the scrape for every
    account tested -- returns a note explaining this, not a silent failure).

    Body: {url: str}. Requires a valid (non-anonymous) identity -- this
    fetches arbitrary caller-supplied URLs server-side, same SSRF-adjacent
    reasoning as gating POST /api/rss/user-feeds behind auth.

    Returns: {resolved: bool, detected_type: str, feed_url: str|None, note: str}
    """
    identity = await _resolve_operator_identity(request)
    if identity["tier"] == "tier0":
        raise HTTPException(status_code=401, detail="A valid token is required to resolve a source.")

    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    result = await _resolve_source(url)
    log.info("rss/resolve-source: %s -> resolved=%s type=%s by %s",
             url, result.get("resolved"), result.get("detected_type"),
             identity.get("user_label"))
    return result


# ── User-defined feed CRUD ────────────────────────────────────────────────────
# _VALID_CATEGORIES retired 2026-08-02 -- categories are dynamic now (built-in
# catalog keys + user-created categories), resolved per-request via
# _list_all_categories(identity) so a caller only sees valid options they can
# actually use (their own personal categories, their department's, and every
# company-wide one).


@app.get("/api/rss/user-feeds")
async def user_feeds_list(request: Request):
    """Return user-defined RSS/Atom feeds visible to the caller.

    UPDATED 2026-08-02: previously returned every saved feed unconditionally
    (single shared global list). Now filtered by shared.rss_catalog.visible_to()
    against the caller's resolved identity -- company-scope feeds (including
    every feed that existed before this change, since unset scope defaults to
    company) still show for everyone; department/personal-scope feeds only
    show to a matching department token or the owning token respectively.
    """
    identity = await _resolve_operator_identity(request)
    return {"feeds": [f for f in _load_user_feeds() if _visible_to(f, identity)]}


@app.post("/api/rss/user-feeds")
async def user_feeds_add(request: Request, body: dict):
    """Add a user-defined feed. Validates the URL fetches a parseable feed first.

    Body: {name: str, url: str, category: str, scope: "company"|"department"|
    "personal" (default "company"), department: str (required if scope=
    "department")}. category must be a valid built-in or user category id
    (resolved per-caller, see _list_all_categories) or "__custom__".

    UPDATED 2026-08-02: requires a resolved identity (Bearer token) --
    anonymous POSTs are rejected with 401. This closes the write exposure
    that made this route reachable/writable from the public internet via
    the now-retired ops.example.com hostname (see the
    tailscale_gate note above) -- defense in depth even after that hostname
    is fully decommissioned at the tunnel level. Also stamps `owner` (this
    caller's token_prefix) so personal-scope feeds are attributable and
    deletable only by their creator (see user_feeds_delete below).
    """
    identity = await _resolve_operator_identity(request)
    if identity["tier"] == "tier0":
        raise HTTPException(status_code=401, detail="A valid token is required to add a feed.")

    name       = (body.get("name") or "").strip()
    url        = (body.get("url")  or "").strip()
    category   = (body.get("category") or "__custom__").strip()
    scope      = (body.get("scope") or "company").strip()
    department = (body.get("department") or "").strip() or None

    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    valid_cats = {c["id"] for c in _list_all_categories(identity)} | {"__custom__"}
    if category not in valid_cats:
        raise HTTPException(status_code=400,
                            detail=f"Invalid category. Valid: {sorted(valid_cats)}")
    if scope not in ("company", "department", "personal"):
        raise HTTPException(status_code=400, detail='scope must be one of: company, department, personal')
    if scope == "department" and not department:
        raise HTTPException(status_code=400, detail="department is required when scope='department'.")

    # Validate the feed fetches successfully before persisting
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            items = await _fetch_one_rss(client, {"name": name or "Feed", "url": url}, "validate")
        if not items:
            raise HTTPException(status_code=422,
                                detail="URL returned 200 but no RSS/Atom items could be parsed.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch feed: {e}")

    async with _user_feeds_lock:
        feeds = _load_user_feeds()
        # Reject exact URL duplicate
        if any(f["url"] == url for f in feeds):
            raise HTTPException(status_code=409, detail="A feed with that URL is already saved.")
        entry = {
            "id":       str(_uuid.uuid4()),
            "name":     name or url,
            "url":      url,
            "category": category,
            "scope":    scope,
            "department": department if scope == "department" else None,
            "owner":    identity.get("token_prefix") if scope == "personal" else None,
            "created_by": identity.get("user_label"),
        }
        feeds.append(entry)
        _save_user_feeds(feeds)

    log.info("user_rss_feeds: added %s → %s (%s, scope=%s) by %s",
              name, url, category, scope, identity.get("user_label"))
    return {"feed": entry, "item_count": len(items)}


@app.delete("/api/rss/user-feeds/{feed_id}")
async def user_feeds_delete(feed_id: str, request: Request):
    """Remove a user-defined feed by its id.

    UPDATED 2026-08-02: requires a resolved identity (same as POST above),
    and additionally requires ownership -- admin tier can delete anything;
    otherwise a company-scope feed can be deleted by anyone with a valid
    token (matches the pre-existing shared-list behavior for the common
    case), a department-scope feed only by a matching department token,
    and a personal-scope feed only by its owning token_prefix.
    """
    identity = await _resolve_operator_identity(request)
    if identity["tier"] == "tier0":
        raise HTTPException(status_code=401, detail="A valid token is required to delete a feed.")

    async with _user_feeds_lock:
        feeds = _load_user_feeds()
        target = next((f for f in feeds if f.get("id") == feed_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Feed not found.")

        if identity["tier"] != "admin":
            scope = target.get("scope") or "company"
            if scope == "personal" and target.get("owner") != identity.get("token_prefix"):
                raise HTTPException(status_code=403, detail="Only the owner can delete a personal feed.")
            if scope == "department" and target.get("department") != identity.get("department"):
                raise HTTPException(status_code=403, detail="Only a matching department token can delete this feed.")

        feeds = [f for f in feeds if f.get("id") != feed_id]
        _save_user_feeds(feeds)
    return {"deleted": feed_id}


# ── Static SPA (must be last) -----------------------------------------------
# index.html: never cache (ensures browser always fetches fresh shell)
# /assets/*:  content-hashed filenames → immutable long-lived cache

import os as _os
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware

class StaticCacheMiddleware(BaseHTTPMiddleware):
    """Add correct Cache-Control headers to static SPA files."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.endswith(".html") or path.endswith("sw.js") \
                or path.endswith("manifest.webmanifest"):
            # Entry points: always revalidate
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"]         = "no-cache"
            response.headers["Expires"]        = "0"
        elif "/assets/" in path and (path.endswith(".js") or path.endswith(".css")):
            # Vite-hashed assets: safe to cache forever
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

app.add_middleware(StaticCacheMiddleware)

# SPA catch-all: serve index.html for any path that isn't an API route or
# an actual static file.  This enables direct-linking and page-refresh on
# client-side routes (/ais, /signals, /trains, etc.) — Starlette's
# StaticFiles html=True only handles directory indexes, not arbitrary SPA paths.
#
# Static assets (/assets/*, sw.js, manifest.webmanifest, etc.) are served
# directly from disk so the browser receives the real JS/CSS, not the shell.
_SPA_INDEX  = _os.path.join(STATIC_DIR, "index.html")
_STATIC_ROOT = _os.path.realpath(STATIC_DIR)

@app.get("/{full_path:path}", include_in_schema=False)
async def _serve_spa(full_path: str):
    # Check if the path maps to a real file inside the static dir (safe against traversal).
    candidate = _os.path.realpath(_os.path.join(STATIC_DIR, full_path))
    if candidate.startswith(_STATIC_ROOT + _os.sep) and _os.path.isfile(candidate):
        return FileResponse(candidate)
    # Everything else is a SPA client-side route — return the app shell.
    if _os.path.isfile(_SPA_INDEX):
        return FileResponse(_SPA_INDEX, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    raise HTTPException(status_code=404, detail="Frontend not built")

if _os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
else:
    log.warning("runner: static dir %s not found -- SPA not served", STATIC_DIR)
