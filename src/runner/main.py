"""
dispatch-runner -- internal operational PWA backend.
FastAPI on port 8001. Tailscale-gated. Serves React static build + API proxy.

Signal proxy fallback chain:
  VDL2 / ACARS / HFDL: local acarshub (:9081) -> jumpseat.acarsdrama.com/api (Jumpseat token)
  AIS:                  local AIS-catcher (:8110) -> MarineTraffic API
  All external fallbacks: 250nm radius centered on KDCA (38.8816, -77.0910)
"""
import asyncio
import ipaddress
import json
import logging
import math
import os

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

# ── Configuration -----------------------------------------------------------
DISPATCH_BASE      = os.getenv("DISPATCH_BASE_URL",        "http://127.0.0.1:8000")
ULTRAFEEDER_URL    = os.getenv("ULTRAFEEDER_URL",           "http://127.0.0.1:8080/data/aircraft.json")
ACARSHUB_URL       = os.getenv("ACARSHUB_URL",             "http://127.0.0.1:9081")
AIS_CATCHER_URL    = os.getenv("AIS_CATCHER_URL",          "http://127.0.0.1:8110")
AIRPLANES_LIVE     = "https://api.airplanes.live/v2"
AIRFRAMES_BASE     = os.getenv("ACARSDRAMA_BASE_URL",      "https://jumpseat.acarsdrama.com/api")
# Token env var: ACARSDRAMA_JUMPSEAT_TOKEN (preferred) or legacy AIRFRAMES_JUMPSEAT_TOKEN
AIRFRAMES_TOKEN    = (os.getenv("ACARSDRAMA_JUMPSEAT_TOKEN") or
                      os.getenv("AIRFRAMES_JUMPSEAT_TOKEN") or "")
MARINETRAFFIC_BASE = os.getenv("MARINETRAFFIC_BASE_URL",   "https://services.marinetraffic.com/api")
MARINETRAFFIC_KEY  = os.getenv("MARINETRAFFIC_API_KEY",    "")
TAILSCALE_CIDR     = ipaddress.ip_network("100.64.0.0/10")
STATIC_DIR         = os.getenv("STATIC_DIR",               "/app/static")
SSE_INTERVAL_SEC   = int(os.getenv("SSE_INTERVAL_SEC",     "30"))

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

def _nm_to_deg(nm: float) -> float:
    """Approximate degrees latitude/longitude per nautical mile."""
    return nm / 60.0

def _bbox(lat: float, lon: float, dist_nm: int) -> dict:
    """Bounding box dict for MarineTraffic area queries."""
    d = _nm_to_deg(dist_nm)
    return {
        "MINLAT": round(lat - d, 4),
        "MAXLAT": round(lat + d, 4),
        "MINLON": round(lon - d, 4),
        "MAXLON": round(lon + d, 4),
    }

def _airframes_headers() -> dict:
    """
    Auth for jumpseat.acarsdrama.com/api.
    Sends both Authorization: Bearer and X-Jumpseat-Token headers since
    the exact scheme is not confirmed in docs -- one will be accepted,
    the other ignored. Adjust if the API returns 401.
    """
    h = {"User-Agent": "corporatetraveldc/1.0",
         "Accept": "application/json"}
    if AIRFRAMES_TOKEN:
        h["Authorization"] = f"Bearer {AIRFRAMES_TOKEN}"
        h["X-Jumpseat-Token"] = AIRFRAMES_TOKEN
    return h


def _airframes_url(endpoint: str) -> str:
    """
    Build endpoint URL for acarsdrama Jumpseat API.
    Base: https://jumpseat.acarsdrama.com/api
    Endpoint paths (verify against API docs -- adjust ACARSDRAMA_*_PATH env vars):
      vdl2  -> /vdl2  or /messages?type=vdl2
      acars -> /acars or /messages?type=acars
      hfdl  -> /hfdl  or /messages?type=hfdl
    """
    path_map = {
        "vdl2":  os.getenv("ACARSDRAMA_VDL2_PATH",  "/vdl2"),
        "acars": os.getenv("ACARSDRAMA_ACARS_PATH",  "/acars"),
        "hfdl":  os.getenv("ACARSDRAMA_HFDL_PATH",   "/hfdl"),
    }
    return AIRFRAMES_BASE.rstrip("/") + path_map.get(endpoint, f"/{endpoint}")

# ── Auth middleware ----------------------------------------------------------

@app.middleware("http")
async def tailscale_gate(request: Request, call_next):
    if request.url.path in ("/healthz",):
        return await call_next(request)
    if not _is_tailscale(_client_ip(request)):
        raise HTTPException(status_code=403, detail="Tailscale access only")
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

async def _airframes_messages(endpoint: str, since: int,
                               lat: float, lon: float, dist: int) -> list:
    """
    Fallback to airframes.io API (Jumpseat token required).
    endpoint: "vdl2" | "acars" | "hfdl"
    Returns list of message dicts.
    """
    if not AIRFRAMES_TOKEN:
        return []
    url = _airframes_url(endpoint)
    params = {"lat": lat, "lon": lon, "radius": dist}
    if since:
        params["since"] = since
    async with httpx.AsyncClient() as c:
        r = await c.get(url, params=params,
                        headers=_airframes_headers(), timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get("messages") or data if isinstance(data, list) else []

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
    except Exception as local_err:
        log.debug("VDL2 local unavailable: %s -- trying acarsdrama.com", local_err)
    try:
        msgs = await _airframes_messages("vdl2", since, lat, lon, dist)
        return {"source": "acarsdrama.com", "messages": msgs, "count": len(msgs)}
    except Exception as ext_err:
        log.warning("VDL2 acarsdrama.com unavailable: %s", ext_err)
    return {"source": "none", "messages": [], "count": 0,
            "detail": "Local acarshub and airframes.io both unavailable"}

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
    except Exception as local_err:
        log.debug("ACARS local unavailable: %s -- trying acarsdrama.com", local_err)
    try:
        msgs = await _airframes_messages("acars", since, lat, lon, dist)
        return {"source": "acarsdrama.com", "messages": msgs, "count": len(msgs)}
    except Exception as ext_err:
        log.warning("ACARS acarsdrama.com unavailable: %s", ext_err)
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
    except Exception as local_err:
        log.debug("HFDL local unavailable: %s -- trying acarsdrama.com", local_err)
    try:
        msgs = await _airframes_messages("hfdl", since, lat, lon, dist)
        return {"source": "acarsdrama.com", "messages": msgs, "count": len(msgs)}
    except Exception as ext_err:
        log.warning("HFDL acarsdrama.com unavailable: %s", ext_err)
    return {"source": "none", "messages": [], "count": 0,
            "detail": "hardware_pending" if not AIRFRAMES_TOKEN else "unavailable"}

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
