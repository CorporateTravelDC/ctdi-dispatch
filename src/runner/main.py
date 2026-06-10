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
from typing import Optional

import httpx
from anthropic import AsyncAnthropic
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

log = logging.getLogger(__name__)

# ── Configuration -----------------------------------------------------------
DISPATCH_BASE      = os.getenv("DISPATCH_BASE_URL",        "http://127.0.0.1:8000")
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
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY",         "")
DISPATCH_CHAT_MODEL = os.getenv("DISPATCH_CHAT_MODEL",      "claude-haiku-4-5-20251001")

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
    if request.url.path in ("/healthz",):
        return await call_next(request)
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

async def _acarshub_messages(msg_type: str, since: int) -> list:
    """
    Fetch messages from local acarshub REST API.
    msg_type: "vdl2" | "acars" | "hfdl"
    Returns list of message dicts or raises.
    """
    url = f"{ACARSHUB_URL}/api/0/all"
    async with httpx.AsyncClient() as c:
        r = await c.get(url, params={"since_message": since}, timeout=6)
        r.raise_for_status()
        data = r.json()
        # acarshub returns {messages: [...], offset: N}
        messages = data.get("messages") or data if isinstance(data, list) else []
        if msg_type == "vdl2":
            return [m for m in messages if m.get("msgtype", "").upper() in ("VDL2", "VDL-2")]
        elif msg_type == "hfdl":
            return [m for m in messages if m.get("msgtype", "").upper() == "HFDL"]
        else:  # acars
            return [m for m in messages if m.get("msgtype", "").upper() == "ACARS"]

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
        return items


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


async def _build_dispatch_context() -> str:
    """Gather current live dispatch state for the AI system prompt."""
    parts: list[str] = []
    async with httpx.AsyncClient() as c:

        # CPS
        try:
            r = await c.get(f"{DISPATCH_BASE}/api/v1/cps", timeout=5)
            if r.status_code == 200:
                cps = r.json()
                parts.append(
                    f"CPS: {cps.get('score','?')} / {cps.get('label','?')} — "
                    f"{cps.get('narrative','')}"
                )
        except Exception:
            parts.append("CPS: unavailable")

        # Weather (METAR)
        try:
            r = await c.get(f"{DISPATCH_BASE}/api/v1/weather", timeout=5)
            if r.status_code == 200:
                wx = r.json()
                stations = wx.get("stations") or {}
                wx_lines = []
                for icao, data in list(stations.items())[:5]:
                    raw = (data.get("raw_text") or "").strip()
                    if raw:
                        wx_lines.append(f"  {icao}: {raw}")
                if wx_lines:
                    parts.append("WEATHER (METAR):\n" + "\n".join(wx_lines))
        except Exception:
            parts.append("WEATHER: unavailable")

        # Active TFRs
        try:
            r = await c.get(f"{DISPATCH_BASE}/api/v1/tfr", timeout=5)
            if r.status_code == 200:
                tfrs = r.json()
                if isinstance(tfrs, list) and tfrs:
                    vip = [t for t in tfrs if t.get("is_vip")]
                    ids = ", ".join(
                        t.get("notam_id") or t.get("id") or "?"
                        for t in tfrs[:5]
                    )
                    parts.append(
                        f"TFRS: {len(tfrs)} active ({len(vip)} VIP/POTUS) — {ids}"
                    )
                else:
                    parts.append("TFRS: none active")
        except Exception:
            parts.append("TFRS: unavailable")

        # Feed health
        try:
            r = await c.get(f"{DISPATCH_BASE}/api/v1/feeds", timeout=5)
            if r.status_code == 200:
                feeds = r.json()
                errors = [
                    k for k, v in feeds.items()
                    if isinstance(v, dict) and v.get("error")
                ]
                stale = [
                    k for k, v in feeds.items()
                    if isinstance(v, dict)
                    and not v.get("error")
                    and (v.get("age_seconds") or 0) > (v.get("stale_threshold_seconds") or 900)
                ]
                if errors:
                    parts.append(f"FEED ERRORS: {', '.join(errors)}")
                elif stale:
                    parts.append(f"FEEDS STALE: {', '.join(stale)}")
                else:
                    parts.append("FEEDS: all nominal")
        except Exception:
            parts.append("FEEDS: unavailable")

        # NWS alerts
        try:
            r = await c.get(f"{DISPATCH_BASE}/api/v1/alerts", timeout=5)
            if r.status_code == 200:
                alerts = r.json()
                if isinstance(alerts, list) and alerts:
                    headlines = "; ".join(
                        a.get("headline") or a.get("event") or "?"
                        for a in alerts[:3]
                    )
                    parts.append(f"NWS ALERTS ({len(alerts)}): {headlines}")
        except Exception:
            pass  # non-critical

    return "\n".join(parts)


@app.post("/api/ask")
async def ask_dispatch(req: AskRequest):
    """
    Streaming dispatch AI chat endpoint.
    Gathers live dispatch context, then streams an Anthropic response via SSE.
    Events: {"type":"text","text":"..."} | {"type":"done"} | {"type":"error","detail":"..."}
    """
    if not ANTHROPIC_API_KEY or len(ANTHROPIC_API_KEY) < 20:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY not configured — add real key to ~/.secrets/anthropic.token and re-run populate-secrets.sh",
        )

    ctx = await _build_dispatch_context()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    system_prompt = f"""You are the dispatch AI assistant for CS Executive Services, LLC — a boutique executive services firm specializing in automotive detailing, brand strategy, executive chauffeur transportation, and IT security. The operator is Corey Sheldon (WA1EM Extra class, ARES NoVA District 10, Skywarn L0344).

You have access to live operational data from the dispatch system. Answer questions about weather, airspace, TFRs, flight status, NOTAMs, ground operations, HEMS go/no-go, and executive transportation planning. Be concise and operationally focused. Think like a professional dispatcher.

CURRENT DISPATCH STATE ({now}):
{ctx}

OPERATOR CONTEXT:
- Location: Arlington County, VA (15 min outside DC / KDCA)
- Primary airspace concern: DC FRZ/SFRA, P-56A/B, concentric rings (50/100/150/250nm)
- Ground operations: Executive chauffeur, Corporate Car Worldwide + Uber Black
- Emergency/radio: ARES NoVA, CERT Fairfax+Loudoun, Skywarn LWX (L0344), GMRS WRCR715
- Pi-based dispatch spine on Tailscale (csexecutiveservices.ts.net)

Respond in plain text. No markdown headers or bullet symbols. Keep responses tactical and brief unless elaboration is explicitly requested. For go/no-go HEMS questions, always cite the CPS score and narrative above."""

    messages: list[dict] = []
    for h in req.history[-20:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": req.message})

    async def stream_response():
        try:
            client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
            async with client.messages.stream(
                model=DISPATCH_CHAT_MODEL,
                max_tokens=1024,
                system=system_prompt,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield f"data: {json.dumps({'type': 'text', 'text': text})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            log.error("dispatch ask error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"

    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Dispatch unavailable: {e}")

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

# ── Static SPA (must be last) -----------------------------------------------

import os as _os
if _os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
else:
    log.warning("runner: static dir %s not found -- SPA not served", STATIC_DIR)
