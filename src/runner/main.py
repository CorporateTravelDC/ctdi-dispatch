"""
dispatch-runner -- internal operational PWA backend.
FastAPI on port 8001. Tailscale-gated. Serves React static build + API proxy.
"""
import asyncio
import ipaddress
import json
import logging
import os

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

DISPATCH_BASE    = os.getenv("DISPATCH_BASE_URL", "http://127.0.0.1:8000")
ULTRAFEEDER_URL  = os.getenv("ULTRAFEEDER_URL",   "http://127.0.0.1:8080/data/aircraft.json")
AIRPLANES_LIVE   = "https://api.airplanes.live/v2"
TAILSCALE_CIDR   = ipaddress.ip_network("100.64.0.0/10")
STATIC_DIR       = os.getenv("STATIC_DIR", "/app/static")
SSE_INTERVAL_SEC = int(os.getenv("SSE_INTERVAL_SEC", "30"))

app = FastAPI(title="dispatch-runner", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Tailscale auth middleware ────────────────────────────────────────────

def _client_ip(request: Request) -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def _is_tailscale(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in TAILSCALE_CIDR
    except ValueError:
        return False


@app.middleware("http")
async def tailscale_gate(request: Request, call_next):
    if request.url.path in ("/healthz",):
        return await call_next(request)
    ip = _client_ip(request)
    if not _is_tailscale(ip):
        log.warning("runner: rejected %s (not Tailscale)", ip)
        raise HTTPException(status_code=403, detail="Tailscale access only")
    return await call_next(request)


# ── Health ───────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "dispatch-runner", "version": "1.0"}


# ── ADS-B proxy ──────────────────────────────────────────────────────────

@app.get("/api/adsb/local")
async def adsb_local():
    """Proxy to local UltraFeeder tar1090 aircraft.json."""
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(ULTRAFEEDER_URL, timeout=5)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"UltraFeeder unavailable: {e}")


@app.get("/api/adsb/live")
async def adsb_live(
    lat: float = Query(38.8816, description="Center latitude"),
    lon: float = Query(-77.0910, description="Center longitude"),
    dist: int  = Query(250,     description="Radius nm"),
):
    """Proxy to airplanes.live API -- full area window regardless of antenna range."""
    url = f"{AIRPLANES_LIVE}/aircraft/lat/{lat}/lon/{lon}/dist/{dist}"
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(url, timeout=12,
                            headers={"User-Agent": "corporatetraveldc/1.0"})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"airplanes.live unavailable: {e}")


# ── Dispatch API transparent proxy ───────────────────────────────────────

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
                r = await c.request(request.method, url, content=body,
                                    headers={**headers,
                                             "Content-Type": request.headers.get(
                                                 "Content-Type", "application/json")},
                                    timeout=10)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Dispatch API unavailable: {e}")


# ── SSE state stream ─────────────────────────────────────────────────────

async def _fetch_state() -> dict:
    """Fetch snapshot of CPS, TFR count, and feed health from dispatch API."""
    result: dict = {}
    async with httpx.AsyncClient() as c:
        for key, path in [("cps", "api/v1/cps"), ("feeds", "api/v1/feeds"),
                          ("tfr", "api/v1/tfr"), ("healthz", "healthz")]:
            try:
                r = await c.get(f"{DISPATCH_BASE}/{path}", timeout=5)
                result[key] = r.json() if r.status_code == 200 else None
            except Exception:
                result[key] = None
    # Summarise TFR for stream efficiency
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
                payload = json.dumps({"type": "state", **state})
                yield f"data: {payload}\n\n"
            except Exception as e:
                log.warning("SSE fetch error: %s", e)
                yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"
            await asyncio.sleep(SSE_INTERVAL_SEC)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache",
                 "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


# ── Static SPA (must be last) ────────────────────────────────────────────

import os as _os
if _os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
else:
    log.warning("runner: static dir %s not found -- SPA not served", STATIC_DIR)
