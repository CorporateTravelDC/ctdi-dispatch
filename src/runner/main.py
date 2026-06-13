"""
dispatch-runner -- internal operational PWA backend.
FastAPI on port 8001. Tailscale-gated. Serves React static build + API proxy.

Signal proxy fallback chain:
  VDL2 / ACARS / HFDL: local acarshub (:9081)
                        -> api.jumpseat.acarsdrama.com/v1 (acarsdrama Jumpseat)
                        -> api.airframes.io (airframes.io, secondary fallback)
  AIS:                  local AIS-catcher (:8110) -> MarineTraffic API
  All external fallbacks: 250nm radius centered on KDCA (38.8816, -77.0910)
"""
import asyncio
import datetime
import ipaddress
import json
import logging
import math
import os
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
AIRFRAMES_BASE     = os.getenv("AIRFRAMES_BASE_URL",        "https://api.airframes.io")
AIRFRAMES_TOKEN    = os.getenv("AIRFRAMES_TOKEN",            "")

MARINETRAFFIC_BASE = os.getenv("MARINETRAFFIC_BASE_URL",    "https://services.marinetraffic.com/api")
MARINETRAFFIC_KEY  = os.getenv("MARINETRAFFIC_API_KEY",     "")
TAILSCALE_CIDR     = ipaddress.ip_network("100.64.0.0/10")
STATIC_DIR         = os.getenv("STATIC_DIR",                "/app/static")
SSE_INTERVAL_SEC   = int(os.getenv("SSE_INTERVAL_SEC",      "30"))

# ── Dispatch AI chat --------------------------------------------------------
# Resolution order: local data → Open WebUI proxy → Ollama direct fallback.
# Both csexec-chat and csexec-osint are Modelfile wrappers on mistral-nemo:latest.
# llama3.2:3b removed. Operator may override per-request via "/model <name> <query>".
OLLAMA_BASE_URL    = os.getenv("OLLAMA_BASE_URL",   "")              # e.g. http://host.containers.internal:11434
OPENWEBUI_URL      = os.getenv("OPENWEBUI_URL",     "")              # e.g. http://127.0.0.1:3000
OPENWEBUI_API_KEY  = os.getenv("OPENWEBUI_API_KEY", "")              # sk-... bearer token
OLLAMA_CHAT_MODEL  = os.getenv("OLLAMA_CHAT_MODEL",  "csexec-chat:latest")  # dispatch drawer (mistral-nemo)
OLLAMA_OSINT_MODEL = os.getenv("OLLAMA_OSINT_MODEL", "csexec-osint:latest") # OSINT narrative (mistral-nemo)
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


@app.middleware("http")
async def tailscale_gate(request: Request, call_next):
    # Admin routes require a trusted source (Tailscale / localhost).
    # All other routes (UI, API data, chat) are open — CF tunnel + CF Access
    # handles edge auth for dispatch-runner.csexecutiveservices.com.
    if request.url.path.startswith("/admin"):
        if not _is_trusted(request):
            return JSONResponse(status_code=403, content={"detail": "access denied"})
    return await call_next(request)

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


async def _airframes_messages(endpoint: str, since: int,
                               lat: float, lon: float, dist: int) -> list:
    """
    Fetch from airframes.io API (secondary external fallback).
    Only called when both local acarshub and acarsdrama are unavailable.
    """
    if not AIRFRAMES_TOKEN:
        return []
    url = f"{AIRFRAMES_BASE.rstrip('/')}/{endpoint}"
    params = {"lat": lat, "lon": lon, "radius": dist}
    if since:
        params["since"] = since
    async with httpx.AsyncClient() as c:
        r = await c.get(url, params=params,
                        headers=_airframes_headers(), timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get("messages") or (data if isinstance(data, list) else [])

# ── VDL2 endpoint -----------------------------------------------------------

@app.get("/api/vdl2/messages")
async def vdl2_messages(
    since: int   = Query(0),
    lat:   float = Query(DEFAULT_LAT),
    lon:   float = Query(DEFAULT_LON),
    dist:  int   = Query(DEFAULT_DIST),
):
    """VDL2 messages. Local acarshub first; falls back to airframes.io."""
    try:
        msgs = await _acarshub_messages("vdl2", since)
        return {"source": "local", "messages": msgs, "count": len(msgs)}
    except Exception as e:
        log.debug("VDL2 local unavailable: %s -- trying acarsdrama", e)
    try:
        msgs = await _acarsdrama_messages("VDLM2", since, lat, lon, dist)
        return {"source": "acarsdrama.com", "messages": msgs, "count": len(msgs)}
    except Exception as e:
        log.debug("VDL2 acarsdrama unavailable: %s -- trying airframes.io", e)
    try:
        msgs = await _airframes_messages("vdl2", since, lat, lon, dist)
        return {"source": "airframes.io", "messages": msgs, "count": len(msgs)}
    except Exception as e:
        log.warning("VDL2 all sources unavailable: %s", e)
    return {"source": "none", "messages": [], "count": 0}

# ── ACARS endpoint ----------------------------------------------------------

@app.get("/api/acars/messages")
async def acars_messages(
    since: int   = Query(0),
    lat:   float = Query(DEFAULT_LAT),
    lon:   float = Query(DEFAULT_LON),
    dist:  int   = Query(DEFAULT_DIST),
):
    """ACARS messages. Local acarshub first; falls back to airframes.io."""
    try:
        msgs = await _acarshub_messages("acars", since)
        return {"source": "local", "messages": msgs, "count": len(msgs)}
    except Exception as e:
        log.debug("ACARS local unavailable: %s -- trying acarsdrama", e)
    try:
        msgs = await _acarsdrama_messages("ACARS", since, lat, lon, dist)
        return {"source": "acarsdrama.com", "messages": msgs, "count": len(msgs)}
    except Exception as e:
        log.debug("ACARS acarsdrama unavailable: %s -- trying airframes.io", e)
    try:
        msgs = await _airframes_messages("acars", since, lat, lon, dist)
        return {"source": "airframes.io", "messages": msgs, "count": len(msgs)}
    except Exception as e:
        log.warning("ACARS all sources unavailable: %s", e)
    return {"source": "none", "messages": [], "count": 0}

# ── HFDL endpoint -----------------------------------------------------------

@app.get("/api/hfdl/messages")
async def hfdl_messages(
    since: int   = Query(0),
    lat:   float = Query(DEFAULT_LAT),
    lon:   float = Query(DEFAULT_LON),
    dist:  int   = Query(DEFAULT_DIST),
):
    """HFDL messages. Local acarshub first; falls back to airframes.io."""
    try:
        msgs = await _acarshub_messages("hfdl", since)
        return {"source": "local", "messages": msgs, "count": len(msgs)}
    except Exception as e:
        log.debug("HFDL local unavailable: %s -- trying acarsdrama", e)
    try:
        msgs = await _acarsdrama_messages("HFDL", since, lat, lon, dist)
        return {"source": "acarsdrama.com", "messages": msgs, "count": len(msgs)}
    except Exception as e:
        log.debug("HFDL acarsdrama unavailable: %s -- trying airframes.io", e)
    try:
        msgs = await _airframes_messages("hfdl", since, lat, lon, dist)
        return {"source": "airframes.io", "messages": msgs, "count": len(msgs)}
    except Exception as e:
        log.warning("HFDL all sources unavailable: %s", e)
    hw = "hardware_pending" if not ACARSDRAMA_TOKEN and not AIRFRAMES_TOKEN else "unavailable"
    return {"source": "none", "messages": [], "count": 0, "detail": hw}

# ── AIS endpoint ------------------------------------------------------------

@app.get("/api/ais/vessels")
async def ais_vessels(
    lat:  float = Query(DEFAULT_LAT),
    lon:  float = Query(DEFAULT_LON),
    dist: int   = Query(DEFAULT_DIST),
):
    """
    AIS vessel positions. Local AIS-catcher first; falls back to MarineTraffic API.
    AIS-catcher exposes vessel JSON at /vessels.json when running.
    """
    # 1 -- Local AIS-catcher
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{AIS_CATCHER_URL}/vessels.json", timeout=5)
            r.raise_for_status()
            data = r.json()
            vessels = data.get("vessels") or data if isinstance(data, list) else []
            return {"source": "local", "vessels": vessels, "count": len(vessels)}
    except Exception as local_err:
        log.debug("AIS local unavailable: %s -- trying MarineTraffic", local_err)

    # 2 -- MarineTraffic API fallback
    if not MARINETRAFFIC_KEY:
        return {"source": "none", "vessels": [], "count": 0,
                "detail": "hardware_pending"}
    try:
        bbox = _bbox(lat, lon, dist)
        url = (f"{MARINETRAFFIC_BASE}/getVessels/v:8/{MARINETRAFFIC_KEY}"
               f"/MINLAT:{bbox['MINLAT']}/MAXLAT:{bbox['MAXLAT']}"
               f"/MINLON:{bbox['MINLON']}/MAXLON:{bbox['MAXLON']}"
               f"/protocol:json")
        async with httpx.AsyncClient() as c:
            r = await c.get(url, timeout=12,
                            headers={"User-Agent": "corporatetraveldc/1.0"})
            r.raise_for_status()
            data = r.json()
            vessels = data.get("DATA") or data if isinstance(data, list) else []
            return {"source": "marinetraffic.com", "vessels": vessels,
                    "count": len(vessels)}
    except Exception as ext_err:
        log.warning("AIS MarineTraffic unavailable: %s", ext_err)

    return {"source": "none", "vessels": [], "count": 0}

# ── Dispatch AI chat --------------------------------------------------------

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
        try:
            payload = {
                "model":    resolved_model,
                "stream":   True,
                "messages": [{"role": "system", "content": system}] + messages,
            }
            yield f"data: {json.dumps({'type': 'model_info', 'model': resolved_model})}\n\n"
            async with httpx.AsyncClient(timeout=120) as c:
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
            "You are the dispatch AI assistant for CS Executive Services, LLC — a boutique executive services firm: "
            "automotive detailing, brand strategy, executive chauffeur transportation, and IT security. "
            "Operator: Corey Sheldon (WA1EM Extra, ARES NoVA District 10, Skywarn L0344).\n\n"
            "All live operational data below comes from a local dispatch spine running on-premises. "
            "It is the authoritative source. Do not speculate beyond it.\n\n"
            f"CURRENT DISPATCH STATE ({now}):\n"
            f"{ctx_str if ctx_str else 'No data available from dispatch spine.'}\n\n"
            "OPERATOR CONTEXT:\n"
            "- Location: Arlington County, VA / KDCA (15 min)\n"
            "- Airspace: DC FRZ/SFRA, P-56A/B, concentric rings 50/100/150/250nm\n"
            "- Ground ops: Corporate Car Worldwide + Uber Black\n"
            "- Emergency: ARES NoVA, CERT Fairfax+Loudoun, Skywarn LWX (L0344), GMRS WRCR715\n"
            "- Dispatch spine: Pi 5, Tailscale (csexecutiveservices.ts.net)\n\n"
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

@app.api_route("/api/dispatch/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy_dispatch(path: str, request: Request):
    """Transparent proxy to dispatch web API on port 8000."""
    url = f"{DISPATCH_BASE}/{path}"
    headers = {}
    auth = request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth
    try:
        async with httpx.AsyncClient() as c:
            if request.method == "GET":
                r = await c.get(url, params=dict(request.query_params),
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
        return r.json()
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

# ── ntfy feed proxy ─────────────────────────────────────────────────────────
# Streams ntfy SSE through the runner so the frontend avoids CORS/auth issues.
# Known topics: tfr-alert, hot-alerts, flight-alerts, cps, ops-health,
#               train-alerts, wx-alerts, osint-alerts, dispatch,
#               dispatch-debriefs, ops-brief

@app.get("/api/ntfy/stream")
async def ntfy_stream(request: Request, topics: str = "dispatch,wx-alerts,flight-alerts,tfr-alert,cps,ops-health,train-alerts"):
    """Proxy ntfy SSE feed to the frontend.

    ?topics=comma,separated,topic,names
    Streams ntfy JSON events as SSE data lines.
    """
    topic_str = topics.replace(" ", "")
    ntfy_sse_url = f"{NTFY_URL.rstrip('/')}/{topic_str}/sse"

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

_RSS_CATALOG: dict[str, list[dict]] = {
    "corporate_intel": [
        {"name": "Business Travel News",    "url": "https://www.businesstravelnews.com/rss/news"},
        {"name": "Skift",                   "url": "https://skift.com/feed/"},
        {"name": "Road Warrior Voices",     "url": "https://roadwarriorvoices.com/feed/"},
    ],
    "marketing_intel": [
        {"name": "Luxury Travel Advisor",   "url": "https://www.luxurytraveladvisor.com/rss/home"},
        {"name": "Travel Weekly",           "url": "https://www.travelweekly.com/rss"},
        {"name": "Hotel Management",        "url": "https://www.hotelmanagement.net/rss/news"},
    ],
    "travel_trends": [
        {"name": "The Points Guy",          "url": "https://thepointsguy.com/feed/"},
        {"name": "Condé Nast Traveler",     "url": "https://www.cntraveler.com/feed/rss"},
        {"name": "Executive Traveller",     "url": "https://www.executivetraveller.com/feed"},
    ],
    "dc_area": [
        {"name": "WTOP Traffic & Transit",  "url": "https://wtop.com/category/traffic/feed/"},
        {"name": "DCist",                   "url": "https://dcist.com/feeds/rss/"},
        {"name": "Greater Greater Wash.",   "url": "https://ggwash.org/feed"},
    ],
    "aviation": [
        {"name": "Simple Flying",           "url": "https://simpleflying.com/feed/"},
        {"name": "Aviation Week",           "url": "https://aviationweek.com/rss"},
        {"name": "AOPA News",               "url": "https://www.aopa.org/news-and-media/all-news/rss"},
    ],
}

_NS = {
    "atom":    "http://www.w3.org/2005/Atom",
    "media":   "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def _parse_rss(xml_bytes: bytes, source_name: str) -> list[dict]:
    """Parse RSS/Atom XML into a list of normalised item dicts."""
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
                          "published": pub, "source": source_name})
        return items

    # RSS 2.0
    for item in root.findall(".//item"):
        title   = (item.findtext("title") or "").strip()
        link    = (item.findtext("link") or "").strip()
        desc    = (item.findtext("description") or "").strip()
        # strip HTML tags from description
        desc    = re.sub(r"<[^>]+>", "", desc)[:280]
        pub     = (item.findtext("pubDate") or item.findtext("dc:date") or "").strip()
        items.append({"title": title, "link": link, "summary": desc,
                      "published": pub, "source": source_name})

    return items


# Simple in-memory RSS cache: (category, url) → (timestamp, items)
_rss_cache: dict[str, tuple[float, list[dict]]] = {}
_RSS_TTL = 900  # 15 minutes


@app.get("/api/rss")
async def rss_feed(category: str = "corporate_intel"):
    """Fetch and return normalised RSS items for a category.

    ?category=corporate_intel|marketing_intel|travel_trends|dc_area|aviation
    """
    if category not in _RSS_CATALOG:
        raise HTTPException(status_code=400,
                            detail=f"Unknown category. Valid: {list(_RSS_CATALOG)}")

    feeds = _RSS_CATALOG[category]
    now   = time.time()
    all_items: list[dict] = []

    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        for feed in feeds:
            cache_key = f"{category}:{feed['url']}"
            cached    = _rss_cache.get(cache_key)
            if cached and (now - cached[0]) < _RSS_TTL:
                all_items.extend(cached[1])
                continue
            try:
                r = await client.get(feed["url"],
                                     headers={"User-Agent": "corporatetraveldc-dispatch/1.0"})
                if r.status_code == 200:
                    parsed = _parse_rss(r.content, feed["name"])
                    _rss_cache[cache_key] = (now, parsed)
                    all_items.extend(parsed)
                else:
                    log.warning("rss: %s returned %d", feed["url"], r.status_code)
            except Exception as e:
                log.warning("rss: fetch %s failed: %s", feed["url"], e)

    # Sort by published desc (best-effort — strings may not sort perfectly)
    all_items.sort(key=lambda x: x.get("published", ""), reverse=True)
    return {"category": category, "count": len(all_items), "items": all_items[:60]}


# ── RSS catalog listing ──────────────────────────────────────────────────────
@app.get("/api/rss/categories")
async def rss_categories():
    """Return available RSS categories and their feed sources."""
    return {
        cat: [{"name": f["name"], "url": f["url"]} for f in feeds]
        for cat, feeds in _RSS_CATALOG.items()
    }


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

if _os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
else:
    log.warning("runner: static dir %s not found -- SPA not served", STATIC_DIR)
