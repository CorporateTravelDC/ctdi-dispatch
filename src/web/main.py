"""
corporatetraveldc web — FastAPI application.

Route structure:
  GET  /healthz                        Tier 0 — public health check
  GET  /api/v1/cps                     Tier 0 — current CPS score
  GET  /api/v1/feeds                   Tier 0 — feed freshness
  GET  /api/v1/events                  Tier 0 — live SSE event stream
  GET  /api/v1/tfr                     Tier 0 — active TFRs
  GET  /api/v1/weather                 Tier 0 — METAR snapshot
  GET  /api/v1/brief                   Tier 0 — latest daily brief text
  GET  /api/v1/route                   Tier 0 — latest route narrative
  GET  /api/v1/airspace                Tier 0 — static DC airspace GeoJSON (SFRA/FRZ/P-56)
  GET  /api/v1/airspace/{id}           Tier 0 — single airspace feature by ID
  GET  /api/v1/demo/readiness          Tier 0 — demo archive seed status

  GET  /api/v1/radio                   Tier 1 (CERT/Tailscale)

  GET  /api/v1/cui/*                   Tier 2 (SHARES) — audit-logged

  GET  /admin/healthz                  Admin
  GET  /admin/feeds                    Admin
  GET  /admin/audit                    Admin
  GET  /admin/tokens                   Admin
  GET  /admin/version                  Admin
  GET  /admin/triggers                 Admin
  POST /admin/refresh-feed/{feed}      Admin
  POST /admin/force-recompute-cps      Admin
  POST /admin/push-alert               Admin  (push-test-alert is a legacy alias)
  GET  /admin/vip                      Admin
  POST /admin/vip                      Admin
  DELETE /admin/vip/{entry}            Admin
"""

import json
import os
import pathlib
import sqlite3
import time
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from auth.auth import Tier, require_admin, require_tier, resolve_tier
from common import config, db
from web.routes.watchlist import router as watchlist_router
from web.routes.fids import router as fids_router
from web.routes.airspace import router as airspace_router
from web.sse import live_events

app = FastAPI(
    title="corporatetraveldc",
    version="1.0.0",
    docs_url=None,   # No public docs — Tailscale-only access.
    redoc_url=None,
)

# Tailscale-only deployment — no public CORS needed.
# Keep permissive for development; tighten at nginx.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(watchlist_router)
app.include_router(fids_router)
app.include_router(airspace_router)

# ── Startup ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    db.init_db()
    db.init_db_v2()
    db.init_db_v3()
    db.init_db_v4()
    db.init_db_v5()
    db.init_db_v6()
    db.init_db_v7()
    db.init_db_v8()
    db.init_db_v9()
    db.init_db_v10()
    db.init_db_v11()
    db.init_db_v12()


# ── Tier 0 — Public (Cloudflare Tunnel + Tailscale) ───────────────────────────

@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Overall health check — Tier 0."""
    feeds = db.get_feed_states()
    cps = db.get_latest_cps()
    now = time.time()

    stale = []
    thresholds = {
        "metar": 900, "tfr": 900, "nas": 900,
        "nws": 2700, "notam": 900, "runsheet": 900, "atcscc_opsplan": 7200,
        "dca_fids": 180, "iad_fids": 180,
    }
    # REST feeds that are covered by a push source — skip staleness check when push is healthy.
    push_covers = {"nws": "push:nws", "tfr": "push:stdds", "nas": "push:tfms", "notam": "push:fns"}
    feed_by_name = {f["feed_name"]: f for f in feeds}

    for f in feeds:
        name = f["feed_name"]
        t = thresholds.get(name, 3600)
        age = now - (f["fetched_at"] or 0) if f["fetched_at"] else None
        if age is None or age > t:
            # Check if a healthy push source is covering this REST feed.
            push_name = push_covers.get(name)
            if push_name:
                push = feed_by_name.get(push_name)
                if push and push["fetched_at"] and not push["error"]:
                    push_age = now - push["fetched_at"]
                    if push_age <= 300:
                        continue  # Push is current — REST staleness is expected.
            stale.append(name)

    snapshot_age = None
    newest_fetch = max((f["fetched_at"] or 0) for f in feeds) if feeds else 0
    if newest_fetch:
        snapshot_age = int(now - newest_fetch)

    status_val = "ok" if not stale else "degraded"
    reason = f"Stale feeds: {', '.join(stale)}" if stale else None

    return JSONResponse({
        "status": status_val,
        "reason": reason,
        "snapshot_age_seconds": snapshot_age,
        "audit_count_24h": db.audit_count_24h(),
        "token_count_active": db.active_token_count(),
        "cps": {
            "score": cps["score"],
            "label": cps["label"],
        } if cps else None,
    })


@app.get("/api/v1/cps")
async def get_cps() -> JSONResponse:
    """Current CPS score — Tier 0."""
    cps = db.get_latest_cps()
    if not cps:
        raise HTTPException(status_code=503, detail="CPS not yet computed")
    return JSONResponse({
        "score": cps["score"],
        "label": cps["label"],
        "factors": {
            "ceiling": cps["ceiling_factor"],
            "visibility": cps["visibility_factor"],
            "wind": cps["wind_factor"],
            "precip": cps["precip_factor"],
            "airspace": cps["airspace_factor"],
            "gdp": cps["gdp_factor"],
        },
        "narrative": cps["narrative"],
        "computed_at": cps["computed_at"],
    })


@app.get("/api/v1/feeds")
async def get_feeds() -> JSONResponse:
    """Feed freshness — Tier 0."""
    feeds = db.get_feed_states()
    now = time.time()

    # Per-feed stale thresholds (seconds) — 2× poll interval as default.
    # Matches the values used in /healthz so stale logic is consistent.
    stale_thresholds: dict[str, int] = {
        "metar": 900, "tfr": 900, "nas": 900,
        "nws": 2700, "notam": 900, "runsheet": 900,
        "atcscc_opsplan": 7200,
        "dca_fids": 180, "iad_fids": 180,
        "push:nws": 300, "push:fdps": 300, "push:stdds": 300,
        "push:fns": 300, "push:itws": 300,
        "push:amtrak": 300,
        "dca_fids": 180, "iad_fids": 180,
    }
    # REST feeds covered by a push source — stale REST is expected when push is live.
    push_covers: dict[str, str] = {"nws": "push:nws", "tfr": "push:stdds", "nas": "push:tfms", "notam": "push:fns"}
    feed_by_name = {f["feed_name"]: f for f in feeds}

    result = []
    for f in feeds:
        name = f["feed_name"]
        age = int(now - f["fetched_at"]) if f["fetched_at"] else None
        threshold = stale_thresholds.get(name, 3600)

        # Determine if this polling feed is covered by a healthy push source.
        push_name = push_covers.get(name)
        push_covered = False
        if push_name:
            push = feed_by_name.get(push_name)
            if push and push["fetched_at"] and not push["error"]:
                push_age = int(now - push["fetched_at"])
                push_covered = push_age <= 300

        result.append({
            "feed_name":              name,
            "fetched_at":             f["fetched_at"],
            "age_seconds":            age,
            "stale_threshold_seconds": threshold,
            "push_covered":           push_covered,
            "error":                  f["error"],
            "consecutive_failures":   f["consecutive_failures"],
        })
    return JSONResponse({"feeds": result})


@app.get("/api/v1/events")
async def get_events(request: Request) -> EventSourceResponse:
    """Live event stream — Tier 0. Emits typed SSE events as data changes."""
    return EventSourceResponse(live_events(request))


@app.get("/api/v1/tfr")
async def get_tfr() -> JSONResponse:
    """Active TFRs — Tier 0. No enriched text at Tier 0."""
    tfrs = db.get_active_tfrs()
    result = [
        {
            "tfr_id": t["tfr_id"],
            "is_vip": bool(t["is_vip"]),
            "effective_start": t["effective_start"],
            "effective_end": t["effective_end"],
            # Enriched text served at Tier 1+.
        }
        for t in tfrs
    ]
    return JSONResponse({"tfrs": result, "count": len(result)})


@app.get("/api/v1/weather")
async def get_weather() -> JSONResponse:
    """METAR snapshot — Tier 0."""
    metars = db.get_metar_snapshot()
    result = [
        {
            "station": m["station"],
            "ceiling_ft": m["ceiling_ft"],
            "visibility_sm": m["visibility_sm"],
            "wind_kt": m["wind_kt"],
            "precip_code": m["precip_code"],
            "obs_time": m["obs_time"],
            "fetched_at": m["fetched_at"],
        }
        for m in metars
    ]
    return JSONResponse({"metars": result})


@app.get("/api/v1/brief")
async def get_brief() -> PlainTextResponse:
    """Latest daily brief — Tier 0."""
    brief_path = pathlib.Path(config.state_dir()) / "daily-brief.txt"
    if not brief_path.exists():
        return PlainTextResponse("No brief available yet.")
    return PlainTextResponse(brief_path.read_text())


@app.get("/api/v1/brief/history")
async def get_brief_history(limit: int = 7, type: Optional[str] = None) -> JSONResponse:
    """Return metadata for the last `limit` briefs. Optional ?type=ops|weekly filter. Tier 0."""
    entries = db.get_brief_history(min(max(limit, 1), 30), brief_type=type)
    return JSONResponse(entries)


@app.get("/api/v1/brief/weekly")
async def get_brief_weekly() -> PlainTextResponse:
    """Latest weekly summary — from DB archive or weekly-summary.txt fallback. Tier 0."""
    rows = db.get_brief_history(1, brief_type="weekly")
    if rows:
        row = db.get_brief_by_id(rows[0]["id"])
        if row:
            return PlainTextResponse(row["content"])
    weekly_path = pathlib.Path(config.state_dir()) / "weekly-summary.txt"
    if weekly_path.exists():
        return PlainTextResponse(weekly_path.read_text())
    return PlainTextResponse("No weekly summary available yet.")


@app.get("/api/v1/brief/{brief_ref}")
async def get_brief_by_ref(brief_ref: str) -> PlainTextResponse:
    """Return brief by integer ID or the most recent brief of a type slug. Tier 0."""
    # Integer → fetch specific archived entry
    try:
        row = db.get_brief_by_id(int(brief_ref))
        if not row:
            return PlainTextResponse("Brief not found.", status_code=404)
        return PlainTextResponse(row["content"])
    except ValueError:
        pass
    # Type slug (e.g. "ep-advance", "ops", custom) → most recent of that type
    rows = db.get_brief_history(1, brief_type=brief_ref)
    if rows:
        row = db.get_brief_by_id(rows[0]["id"])
        if row:
            return PlainTextResponse(row["content"])
    return PlainTextResponse(f"No {brief_ref} brief available yet.", status_code=404)


@app.get("/api/v1/route")
async def get_route() -> JSONResponse:
    """Latest route impact narrative — Tier 0."""
    route = db.get_latest_route_narrative()
    if not route:
        raise HTTPException(status_code=503, detail="Route narrative not yet computed")
    return JSONResponse({
        "narrative": route["route_narrative"],
        "active_tfrs": json.loads(route["active_tfrs"] or "[]"),
        "vip_flags": json.loads(route["vip_flags"] or "[]"),
        "computed_at": route["computed_at"],
    })




@app.get("/api/v1/alerts")
async def get_alerts() -> JSONResponse:
    """Active NWS hazardous weather alerts — Tier 0."""
    alerts = db.get_active_nws_alerts()
    result = [
        {
            "alert_id": a["alert_id"],
            "event_type": a["event_type"],
            "area_desc": a["area_desc"],
            "severity": a["severity"],
            "certainty": a["certainty"],
            "effective": a["effective"],
            "expires": a["expires"],
            "headline": a["headline"],
        }
        for a in alerts
    ]

    return JSONResponse({"alerts": result, "count": len(result)})


@app.get("/api/v1/wx/discussion")
async def get_wx_discussion(
    product: Optional[str] = Query(
        default=None,
        description="AWIPS ID: FXUS02 (short-range default), FXUS06 (medium), "
                    "FXUS07 (extended), FXUS05 (QPF). Omit for all products."
    )
) -> JSONResponse:
    """Latest WPC national forecast discussion(s) -- Tier 0."""
    if product:
        awips_id = product.upper()
        row = db.get_latest_wpc_discussion(awips_id)
        if not row:
            return JSONResponse({
                "awips_id": awips_id, "product_label": None,
                "issued_at": None, "fetched_at": None,
                "body": None, "body_excerpt": None, "available": False,
            })
        return JSONResponse({
            "awips_id":      row["awips_id"],
            "product_label": row["product_label"],
            "issued_at":     row["issued_at"],
            "fetched_at":    row["fetched_at"],
            "body":          row["body"],
            "body_excerpt":  (row["body"] or "")[:300],
            "available":     True,
        })
    else:
        rows = db.get_latest_wpc_discussions()
        if not rows:
            return JSONResponse({"discussions": [], "available": False})
        return JSONResponse({
            "discussions": [
                {
                    "awips_id":      r["awips_id"],
                    "product_label": r["product_label"],
                    "issued_at":     r["issued_at"],
                    "fetched_at":    r["fetched_at"],
                    "body_excerpt":  (r["body"] or "")[:300],
                }
                for r in rows
            ],
            "available": True,
        })


@app.get("/api/v1/wx/discussion/{awips_id}")
async def get_wx_discussion_by_id(awips_id: str) -> JSONResponse:
    """Path-form convenience: /api/v1/wx/discussion/FXUS02 -- Tier 0."""
    row = db.get_latest_wpc_discussion(awips_id.upper())
    if not row:
        raise HTTPException(status_code=404,
                            detail=f"No discussion found for {awips_id.upper()}")
    return JSONResponse({
        "awips_id":      row["awips_id"],
        "product_label": row["product_label"],
        "issued_at":     row["issued_at"],
        "fetched_at":    row["fetched_at"],
        "body":          row["body"],
        "body_excerpt":  (row["body"] or "")[:300],
        "available":     True,
    })


@app.get("/api/v1/notams")
async def get_notams() -> JSONResponse:
    """Active NOTAMs for DC-area airports — Tier 0."""
    notams = db.get_active_notams()
    result = [
        {
            "notam_id": n["notam_id"],
            "facility": n["facility"],
            "classification": n["classification"],
            "effective_start": n["effective_start"],
            "effective_end": n["effective_end"],
            "text_body": n["text_body"],
        }
        for n in notams
    ]
    return JSONResponse({"notams": result, "count": len(result)})


@app.get("/api/v1/amtrak")
async def get_amtrak() -> JSONResponse:
    """Latest Amtrak DC-area status — Tier 0."""
    status = db.get_latest_amtrak_status()
    if not status:
        return JSONResponse({"available": False, "summary": "No data yet", "trains": []})
    trains: list = []
    raw = status.get("trains_json")
    if raw:
        try:
            trains = json.loads(raw)
        except Exception:
            trains = []
    return JSONResponse({
        "available": True,
        "summary": status["delay_summary"],
        "fetched_at": status["fetched_at"],
        "trains": trains,
    })


@app.get("/api/v1/train-config")
async def get_train_config() -> JSONResponse:
    """Operator rail config — primary station, regional filter, map center — Tier 0."""
    # Coordinates for common Amtrak stations (used to center the map).
    _COORDS: dict = {
        "WAS": [38.897, -77.006], "NYP": [40.750, -73.993],
        "PHL": [39.955, -75.182], "BOS": [42.366, -71.062],
        "BAL": [39.285, -76.622], "NHV": [41.297, -72.927],
        "SPG": [42.103, -72.590], "NLC": [41.310, -72.924],
        "CHI": [41.879, -87.640], "MKE": [43.001, -87.907],
        "MIN": [44.977, -93.264], "MSP": [44.977, -93.264],
        "SEA": [47.579, -122.331], "PDX": [45.528, -122.678],
        "EMY": [37.834, -122.293], "SFO": [37.776, -122.416],
        "LAX": [34.055, -118.235], "SAN": [32.715, -117.156],
        "DEN": [39.751, -104.999], "SLC": [40.776, -111.887],
        "ABQ": [35.060, -106.649], "NOL": [29.950, -90.072],
        "HOU": [29.753, -95.365], "SAC": [38.584, -121.494],
        "ATL": [33.748, -84.391], "MIA": [25.779, -80.187],
        "ORL": [28.479, -81.379], "CLT": [35.228, -80.843],
        "RVR": [33.980, -117.377], "BWI": [39.167, -76.668],
        "ALB": [42.734, -73.752], "PVD": [41.823, -71.413],
        "BUF": [42.877, -78.879], "SAV": [32.083, -81.093],
    }
    _DEFAULT_ROUTES   = [
        "Acela", "Northeast Regional", "Palmetto", "Carolinian",
        "Vermonter", "Keystone", "Empire Service", "Empire State",
        "Silver Star", "Silver Meteor",
    ]
    _DEFAULT_STATIONS = ["WAS", "BWI", "NCR", "ALX", "BAL", "ABE", "WIL", "NPN"]
    _DEFAULT_CORE     = ["Acela", "Northeast Regional"]

    raw_st = config.get("AMTRAK_REGIONAL_STATIONS", "").strip()
    stations = [s.strip().upper() for s in raw_st.split(",") if s.strip()] if raw_st else _DEFAULT_STATIONS

    raw_rt = config.get("AMTRAK_REGIONAL_ROUTES", "").strip()
    routes = [r.strip() for r in raw_rt.split(",") if r.strip()] if raw_rt else _DEFAULT_ROUTES

    raw_cr = config.get("AMTRAK_CORE_ROUTES", "").strip()
    core = [r.strip() for r in raw_cr.split(",") if r.strip()] if raw_cr else _DEFAULT_CORE

    primary = config.get("AMTRAK_PRIMARY_STATION", "WAS").strip().upper() or "WAS"
    center  = _COORDS.get(primary, _COORDS["WAS"])

    return JSONResponse({
        "primary_station": primary,
        "stations":        stations,
        "routes":          routes,
        "core_routes":     core,
        "center":          center,
        "zoom":            7,
    })


@app.get("/api/v1/data-usage")
async def get_data_usage(days: int = 30) -> JSONResponse:
    """Network data usage from vnstat CSV log — Tier 0.

    Returns per-interface daily totals plus a summary for the requested window.
    ?days=N  — number of days to include (default 30, max 90).
    """
    import csv as _csv
    usage_path = pathlib.Path(config.state_dir()) / "data-usage.csv"
    if not usage_path.exists():
        return JSONResponse({"available": False, "message": "No data-usage log yet."})

    days = min(max(int(days), 1), 90)
    from datetime import date as _date, timedelta as _td
    cutoff = (_date.today() - _td(days=days - 1)).isoformat()

    rows: list[dict] = []
    totals: dict[str, dict] = {}
    try:
        with usage_path.open() as f:
            for row in _csv.DictReader(f):
                if row["date"] < cutoff:
                    continue
                rows.append(row)
                iface = row["interface"]
                if iface not in totals:
                    totals[iface] = {"rx_gb": 0.0, "tx_gb": 0.0, "total_gb": 0.0}
                totals[iface]["rx_gb"]    += float(row.get("rx_gb", 0))
                totals[iface]["tx_gb"]    += float(row.get("tx_gb", 0))
                totals[iface]["total_gb"] += float(row.get("total_gb", 0))
    except Exception as exc:
        return JSONResponse({"available": False, "message": str(exc)})

    # Round totals
    for iface in totals:
        for k in totals[iface]:
            totals[iface][k] = round(totals[iface][k], 4)

    grand_total = round(sum(t["total_gb"] for t in totals.values()), 4)

    return JSONResponse({
        "available":    True,
        "window_days":  days,
        "grand_total_gb": grand_total,
        "by_interface": totals,
        "daily":        rows,
        "log_path":     str(usage_path),
    })


@app.get("/api/v1/demo/readiness")
async def get_demo_readiness() -> JSONResponse:
    """Demo archive seed status — Tier 0.

    Returns how many calendar days of data the recorder has collected,
    whether the 14-day seed target has been reached, and per-tier readiness
    for 2w / 8w / 12w / 24w / 36w / 52w marketing snapshot windows.
    """
    DEMO_DB     = "/var/lib/corporatetraveldc/demo.db"
    SEED_TARGET = 14
    # Retention tiers: label → days required
    TIERS = {
        "2w":  14,   # seed / always-ready buffer
        "8w":  56,   # bi-monthly
        "12w": 84,   # quarterly (3 months)
        "24w": 168,  # semi-annual (6 months)
        "36w": 252,  # 9 months
        "52w": 364,  # annual (12 months)
    }
    from datetime import datetime, timezone, timedelta
    try:
        db_conn = sqlite3.connect(f"file:{DEMO_DB}?mode=ro", uri=True)
        days    = db_conn.execute(
            "SELECT COUNT(DISTINCT DATE(captured_at)) FROM snapshots"
        ).fetchone()[0]
        total   = db_conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        oldest  = (db_conn.execute("SELECT MIN(captured_at) FROM snapshots").fetchone()[0] or "")[:10]
        newest  = (db_conn.execute("SELECT MAX(captured_at) FROM snapshots").fetchone()[0] or "")[:10]
        # Per-tier: how many calendar-day slots have data in that window?
        tiers: dict = {}
        now_utc = datetime.now(timezone.utc)
        for label, target_days in TIERS.items():
            cutoff = (now_utc - timedelta(days=target_days)).isoformat()
            avail  = db_conn.execute(
                "SELECT COUNT(DISTINCT DATE(captured_at)) FROM snapshots WHERE captured_at >= ?",
                (cutoff,)
            ).fetchone()[0]
            tiers[label] = {
                "days_required":  target_days,
                "days_available": avail,
                "ready":          avail >= target_days,
            }
        db_conn.close()
        size_mb = round(os.path.getsize(DEMO_DB) / 1e6, 1) if os.path.exists(DEMO_DB) else 0.0
        return JSONResponse({
            "seed_days":       days,
            "seed_target":     SEED_TARGET,
            "ready":           days >= SEED_TARGET,
            "total_snapshots": total,
            "oldest":          oldest or None,
            "newest":          newest or None,
            "db_size_mb":      size_mb,
            "retention_days":  364,
            "tiers":           tiers,
        })
    except Exception as exc:
        return JSONResponse(
            {"seed_days": 0, "ready": False, "error": str(exc)},
            status_code=503,
        )


# ── Runsheet + Watchlist (Tier 1) ─────────────────────────────────────────────

@app.get("/api/v1/runsheet")
async def get_runsheet(
    run_date: Optional[str] = Query(default=None,
        description="YYYY-MM-DD — omit for today"),
    tier: Tier = Depends(require_tier(Tier.T1)),
) -> JSONResponse:
    """Daily runsheet — scheduled trips + watchlist sessions for a calendar day."""
    from datetime import date as _date
    import json as _json
    target = run_date or _date.today().isoformat()
    sheet = db.get_runsheet(target)
    active = db.get_active_watchlists(target)
    terminated = db.get_terminated_watchlists(target)
    trips = _json.loads(sheet["scheduled_trips"]) if sheet and sheet.get("scheduled_trips") else []
    return JSONResponse({
        "run_date": target,
        "scheduled_trips": trips,
        "trip_count": len(trips),
        "active_watchlists": [
            {"id": w["id"], "session_type": w["session_type"],
             "subject": w["subject"], "started_at": w["started_at"],
             "session_data": _json.loads(w["session_data"] or "{}")}
            for w in active
        ],
        "terminated_watchlists": [
            {"id": w["id"], "session_type": w["session_type"],
             "subject": w["subject"], "started_at": w["started_at"],
             "terminated_at": w["terminated_at"],
             "terminal_summary": w["terminal_summary"]}
            for w in terminated
        ],
    })


class WatchlistStartRequest(BaseModel):
    session_type: str
    subject: str
    run_date: Optional[str] = None


@app.post("/api/v1/watchlist", status_code=201)
async def start_watchlist(
    body: WatchlistStartRequest,
    tier: Tier = Depends(require_tier(Tier.T1)),
) -> JSONResponse:
    """Start a flight, train, or custom watchlist session for the current runsheet day."""
    import uuid as _uuid
    from datetime import date as _date
    valid = {"flight", "train", "custom"}
    if body.session_type not in valid:
        raise HTTPException(400, f"session_type must be one of {valid}")
    if not body.subject.strip():
        raise HTTPException(400, "subject is required")
    session_id = str(_uuid.uuid4())
    run_date = body.run_date or _date.today().isoformat()
    db.create_watchlist_session(session_id, body.session_type,
                                body.subject.strip().upper(), run_date)

    # Confirmation push via ntfy
    try:
        import requests as _req
        from common import config as _cfg
        _subj = body.subject.strip().upper()
        _type = body.session_type
        _icon = {"flight": "✈️", "train": "🚆", "custom": "👁"}.get(_type, "👁")
        _msg = f"{_icon} Watchlist ACTIVE: {_type.upper()} {_subj}\nMonitoring started. You will be notified on landing/arrival."
        _headers = {
            "Content-Type": "text/plain",
            "X-Title": f"Watchlist: {_subj}",
            "X-Priority": "3",
            "X-Tags": "eyes",
        }
        _token = _cfg.ntfy_token()
        if _token:
            _headers["Authorization"] = f"Bearer {_token}"
        _req.post(f"{_cfg.ntfy_url()}/flight-alerts", data=_msg.encode(),
                  headers=_headers, timeout=5)
    except Exception:
        pass  # Never fail the API response due to push error

    return JSONResponse({"id": session_id, "status": "active",
                         "session_type": body.session_type,
                         "subject": body.subject.strip().upper(),
                         "run_date": run_date}, status_code=201)


@app.get("/api/v1/watchlist")
async def list_watchlists(
    tier: Tier = Depends(require_tier(Tier.T1)),
) -> JSONResponse:
    """List all currently active watchlist sessions."""
    import json as _json
    active = db.get_active_watchlists()
    return JSONResponse({"active": [
        {"id": w["id"], "session_type": w["session_type"],
         "subject": w["subject"], "run_date": w["run_date"],
         "started_at": w["started_at"],
         "session_data": _json.loads(w["session_data"] or "{}")}
        for w in active
    ], "count": len(active)})


class WatchlistTerminateRequest(BaseModel):
    terminal_summary: Optional[str] = None


@app.delete("/api/v1/watchlist/{session_id}")
async def terminate_watchlist(
    session_id: str,
    body: WatchlistTerminateRequest = WatchlistTerminateRequest(),
    tier: Tier = Depends(require_tier(Tier.T1)),
) -> JSONResponse:
    """Terminate a watchlist session. Data is preserved in the runsheet."""
    session = db.get_watchlist_session(session_id)
    if not session:
        raise HTTPException(404, f"Session {session_id!r} not found")
    if session["status"] == "terminated":
        raise HTTPException(409, "Session already terminated")
    summary = body.terminal_summary or (
        f"{session['session_type'].title()} {session['subject']} monitoring completed.")
    db.terminate_watchlist_session(session_id, summary)
    return JSONResponse({"id": session_id, "status": "terminated",
                         "terminal_summary": summary,
                         "run_date": session["run_date"]})


# ── ATCSCC Ops Plan (Tier 0 + Tier 1 range) ───────────────────────────────────

@app.get("/api/v1/opsplan")
async def get_opsplan(
    plan_date: Optional[str] = Query(default=None,
        description="YYYY-MM-DD — omit for latest"),
) -> JSONResponse:
    """ATCSCC daily ops plan snapshot with pattern tags. Historical dates kept indefinitely."""
    import json as _json
    plan = db.get_atcscc_opsplan(plan_date)
    if not plan:
        raise HTTPException(404, "No ops plan data for requested date")
    return JSONResponse({
        "plan_date": plan["plan_date"],
        "nas_programs": _json.loads(plan["nas_programs"] or "[]"),
        "notam_count": plan["notam_count"],
        "active_airports": _json.loads(plan["active_airports"] or "[]"),
        "pattern_tags": _json.loads(plan["pattern_tags"] or "[]"),
        "weather_summary": plan["weather_summary"],
        "fetched_at": plan["fetched_at"],
    })


@app.get("/api/v1/opsplan/range")
async def get_opsplan_range(
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
    tier: Tier = Depends(require_tier(Tier.T1)),
) -> JSONResponse:
    """ATCSCC ops plan for a date range — for pattern analysis. Tier 1 required."""
    import json as _json
    plans = db.get_atcscc_opsplan_range(start, end)
    return JSONResponse({
        "range": {"start": start, "end": end},
        "days": [
            {"plan_date": p["plan_date"],
             "program_count": len(_json.loads(p["nas_programs"] or "[]")),
             "pattern_tags": _json.loads(p["pattern_tags"] or "[]"),
             "active_airports": _json.loads(p["active_airports"] or "[]"),
             "weather_summary": p["weather_summary"]}
            for p in plans
        ],
        "count": len(plans),
    })

# ── OSINT (Tier 0 — read; Tier 0 — write scopes behind same gate as watchlist) ──

class OsintScopeRequest(BaseModel):
    label:          str
    scope_type:     str = "keyword"
    query_terms:    str
    feed_urls:      str = ""
    push_threshold: str = "HIGH"


@app.get("/api/v1/osint/feed")
async def osint_feed(
    scope_id: Optional[int] = Query(default=None),
    min_score: int = Query(default=0, ge=0, le=10),
    limit: int = Query(default=50, le=200),
) -> JSONResponse:
    """Recent OSINT items, newest first. Filter by scope_id and/or min_score."""
    items = db.osint_get_feed(scope_id=scope_id, min_score=min_score, limit=limit)
    return JSONResponse({"items": items, "count": len(items)})


@app.get("/api/v1/osint/scopes")
async def osint_list_scopes() -> JSONResponse:
    """Return all OSINT scopes (enabled and disabled)."""
    scopes = db.osint_get_scopes(enabled_only=False)
    return JSONResponse({"scopes": scopes, "count": len(scopes)})


@app.post("/api/v1/osint/scopes", status_code=201)
async def osint_create_scope(body: OsintScopeRequest) -> JSONResponse:
    """Create a new OSINT monitoring scope."""
    allowed_types = {
        # Generic
        "keyword", "person", "org", "topic", "geo",
        # Executive-protection context — get DC-area geo boost + EP narrative framing
        "ep_threat", "ep_principal", "ep_venue", "executive_protection",
        # Marketing / brand-intelligence context — CS ExecSvcs brand narrative framing
        "brand_monitor", "market_intel", "competitor", "marketing",
    }
    if body.scope_type not in allowed_types:
        raise HTTPException(400, f"scope_type must be one of {sorted(allowed_types)}")
    allowed_thresholds = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    if body.push_threshold not in allowed_thresholds:
        raise HTTPException(400, f"push_threshold must be one of {sorted(allowed_thresholds)}")
    scope_id = db.osint_add_scope(
        label=body.label.strip(),
        scope_type=body.scope_type,
        query_terms=body.query_terms.strip(),
        feed_urls=body.feed_urls.strip(),
        push_threshold=body.push_threshold,
    )
    return JSONResponse({"id": scope_id, "status": "created"})


@app.patch("/api/v1/osint/scopes/{scope_id}")
async def osint_update_scope(
    scope_id: int,
    body: dict,
) -> JSONResponse:
    """Partially update a scope (label, query_terms, feed_urls, push_threshold, enabled)."""
    if not db.osint_get_scope(scope_id):
        raise HTTPException(404, f"Scope {scope_id} not found")
    db.osint_update_scope(scope_id, **body)
    return JSONResponse({"id": scope_id, "status": "updated"})


@app.delete("/api/v1/osint/scopes/{scope_id}")
async def osint_delete_scope(scope_id: int) -> JSONResponse:
    """Delete an OSINT scope and all its items."""
    if not db.osint_get_scope(scope_id):
        raise HTTPException(404, f"Scope {scope_id} not found")
    db.osint_delete_scope(scope_id)
    return JSONResponse({"id": scope_id, "status": "deleted"})


# ── Tier 1 — CERT / Tailscale ─────────────────────────────────────────────────

@app.get("/api/v1/radio")
async def get_radio(
    tier: Tier = Depends(require_tier(Tier.T1))
) -> JSONResponse:
    """
    Radio reference data — Tier 1 (CERT/Tailscale).
    Returns placeholder structure. Operator populates from credentialed sources on Pi.
    CUI rules: no actual SHARES/HEARS/HEART frequencies here. Ever.
    """
    return JSONResponse({
        "note": "Credentialed radio data is operator-populated. "
                "See /etc/corporatetraveldc/radio-reference/ on the Pi.",
        "placeholder": True,
    })


@app.get("/api/v1/tfr-enriched")
async def get_tfr_enriched(
    tier: Tier = Depends(require_tier(Tier.T1))
) -> JSONResponse:
    """Active TFRs with enrichment text — Tier 1."""
    tfrs = db.get_active_tfrs()
    result = [
        {
            "tfr_id": t["tfr_id"],
            "is_vip": bool(t["is_vip"]),
            "effective_start": t["effective_start"],
            "effective_end": t["effective_end"],
            "enriched_text": t["enriched_text"],
            "enriched_at": t["enriched_at"],
        }
        for t in tfrs
    ]
    return JSONResponse({"tfrs": result, "count": len(result)})


# ── Tier 2 — SHARES (audit-logged) ────────────────────────────────────────────

@app.get("/api/v1/cui/status")
async def get_cui_status(
    request: Request,
    tier: Tier = Depends(require_tier(Tier.T2)),
) -> JSONResponse:
    """
    CUI status endpoint — Tier 2. Audit-logged.
    Returns only placeholder confirmation — actual credentialed data lives on Pi,
    operator-populated. No frequencies here. CUI rules absolute.
    """
    # Get token prefix from Authorization header for audit.
    auth_header = request.headers.get("Authorization", "")
    token_raw = auth_header.removeprefix("Bearer ").strip()
    token_prefix = token_raw[:12] if token_raw else None

    db.audit(
        action="cui_status_read",
        tier=tier.value,
        token_prefix=token_prefix,
        remote_addr=request.client.host if request.client else None,
        detail={"path": "/api/v1/cui/status"},
    )

    return JSONResponse({
        "placeholder": True,
        "note": "CUI data is operator-populated on the Pi. "
                "This endpoint confirms Tier 2 auth is working.",
    })


# ── FAA Aircraft Registry — Tier 0 ────────────────────────────────────────────

@app.get("/api/v1/aircraft/{identifier}")
async def get_aircraft(identifier: str) -> JSONResponse:
    """Look up an aircraft by N-number or ICAO hex from the local FAA registry cache.

    - N-number:  N12345, 12345 (leading N optional)
    - ICAO hex:  a1b2c3  (6 hex chars, case-insensitive)

    Returns registrant name, city/state, aircraft type, hex, LADD flag, and
    registration status.  Returns 404 if not found or if the registry has not
    been imported yet.
    """
    try:
        db.init_db_v11()
    except Exception:
        pass

    ident = identifier.strip()
    record: dict | None = None

    import re as _re
    if _re.fullmatch(r"[0-9a-fA-F]{6}", ident):
        # Looks like a hex code
        record = db.faa_lookup_by_hex(ident)
    else:
        record = db.faa_lookup_by_n_number(ident)

    if not record:
        # Check if registry is populated at all
        counts = db.faa_registry_count()
        if counts["total"] == 0:
            return JSONResponse(
                {"error": "FAA registry not yet imported — first import runs Monday 02:00 ET"},
                status_code=503,
            )
        return JSONResponse({"error": f"Aircraft '{ident}' not found in FAA registry"}, status_code=404)

    return JSONResponse({
        "n_number":        record.get("n_number"),
        "mode_s_hex":      record.get("mode_s_hex"),
        "registrant_name": record.get("registrant_name"),
        "city":            record.get("city"),
        "state":           record.get("state"),
        "year_mfr":        record.get("year_mfr"),
        "mfr_mdl_code":    record.get("mfr_mdl_code"),
        "serial_number":   record.get("serial_number"),
        "status_code":     record.get("status_code"),
        "type_aircraft":   record.get("type_aircraft"),
        "type_engine":     record.get("type_engine"),
        "expiration_date": record.get("expiration_date"),
        "last_action_date":record.get("last_action_date"),
        "ladd":            record.get("ladd", False),
    })


@app.get("/api/v1/aircraft-registry/status")
async def get_faa_registry_status() -> JSONResponse:
    """Return FAA registry import status and record counts."""
    try:
        db.init_db_v11()
        counts = db.faa_registry_count()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse(counts)


# ── Admin — all endpoints require Admin tier ───────────────────────────────────

@app.get("/admin/healthz")
async def admin_healthz(
    tier: Tier = Depends(require_admin)
) -> JSONResponse:
    """Admin health — includes token count and audit tail."""
    feeds = db.get_feed_states()
    return JSONResponse({
        "status": "ok",
        "feed_count": len(feeds),
        "audit_count_24h": db.audit_count_24h(),
        "token_count_active": db.active_token_count(),
    })


@app.get("/admin/feeds")
async def admin_feeds(
    tier: Tier = Depends(require_admin)
) -> JSONResponse:
    feeds = db.get_feed_states()
    return JSONResponse({"feeds": feeds})


@app.get("/admin/audit")
async def admin_audit(
    limit: int = Query(default=50, le=500),
    since: Optional[float] = Query(default=None),
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    rows = db.get_audit_log(limit=limit, since=since)
    return JSONResponse({"audit": rows, "count": len(rows)})


@app.get("/admin/tokens")
async def admin_tokens(
    active_only: bool = Query(default=True),
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    tokens = db.list_tokens(active_only=active_only)
    # Never return token_hash — return prefix + metadata only.
    safe = [
        {
            "id": t["id"],
            "token_prefix": t["token_prefix"],
            "user_label": t["user_label"],
            "tier": t["tier"],
            "device_label": t["device_label"],
            "created_at": t["created_at"],
            "expires_at": t["expires_at"],
            "revoked_at": t["revoked_at"],
        }
        for t in tokens
    ]
    return JSONResponse({"tokens": safe, "count": len(safe)})


@app.get("/admin/version")
async def admin_version(
    tier: Tier = Depends(require_admin)
) -> JSONResponse:
    return JSONResponse({
        "version": "1.0.0",
        "components": ["web", "poller", "pusher", "ctdc-token"],
    })


@app.get("/admin/triggers")
async def admin_triggers(
    outcome: Optional[str] = Query(default=None),
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    in_flight = db.get_triggers(outcome="in_flight", limit=20)
    recent = db.get_triggers(outcome=outcome or "success", limit=20)
    return JSONResponse({
        "in_flight": in_flight,
        "recent_processed": recent,
    })


class RefreshFeedRequest(BaseModel):
    pass  # Body optional; feed_name is path param.


@app.post("/admin/refresh-feed/{feed_name}")
async def refresh_feed(
    feed_name: str,
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    """
    Drop a trigger file for the poller reactor to pick up.
    Returns 202 Accepted — poll /admin/triggers for outcome.
    """
    polled_feeds = {"metar", "tfr", "nas", "nws", "notam", "amtrak", "runsheet", "atcscc_opsplan"}
    if feed_name not in polled_feeds:
        raise HTTPException(
            status_code=400,
            detail=f"{feed_name!r} is not a polled feed. "
                   f"SWIM feeds are broker-pushed and cannot be manually refreshed.",
        )

    trigger_id = str(uuid.uuid4())
    trigger_dir = pathlib.Path(config.trigger_dir())
    trigger_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": trigger_id, "type": "refresh_feed",
               "payload": {"feed_name": feed_name}}
    (trigger_dir / f"{trigger_id}.json").write_text(json.dumps(payload))
    db.insert_trigger(trigger_id, "refresh_feed", {"feed_name": feed_name})

    return JSONResponse(
        {"trigger_id": trigger_id, "status": "accepted"},
        status_code=202,
    )


@app.post("/admin/force-recompute-cps")
async def force_recompute_cps(
    tier: Tier = Depends(require_admin)
) -> JSONResponse:
    trigger_id = str(uuid.uuid4())
    trigger_dir = pathlib.Path(config.trigger_dir())
    trigger_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": trigger_id, "type": "force_recompute_cps", "payload": {}}
    (trigger_dir / f"{trigger_id}.json").write_text(json.dumps(payload))
    db.insert_trigger(trigger_id, "force_recompute_cps", {})

    return JSONResponse(
        {"trigger_id": trigger_id, "status": "accepted"},
        status_code=202,
    )


class TestAlertRequest(BaseModel):
    message: str
    topic: str = "ops-health"   # ntfy topic; default preserves legacy behavior
    title: Optional[str] = None
    priority: int = 3


@app.post("/admin/force-opsplan-snapshot")
async def force_opsplan_snapshot(
    plan_date: Optional[str] = Query(default=None,
        description="YYYY-MM-DD — omit for today. Use for backfill."),
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    """Force an immediate ATCSCC ops plan snapshot. Optionally specify date for backfill."""
    trigger_id = str(uuid.uuid4())
    trigger_dir = pathlib.Path(config.trigger_dir())
    trigger_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": trigger_id, "type": "force_opsplan_snapshot",
               "payload": {"plan_date": plan_date}}
    (trigger_dir / f"{trigger_id}.json").write_text(json.dumps(payload))
    db.insert_trigger(trigger_id, "force_opsplan_snapshot",
                      {"plan_date": plan_date})
    return JSONResponse(
        {"trigger_id": trigger_id, "status": "accepted",
         "plan_date": plan_date or "today"},
        status_code=202,
    )


@app.post("/admin/force-osint-scrape")
async def force_osint_scrape(
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    """Force an immediate OSINT scrape pass across all enabled scopes."""
    trigger_id = str(uuid.uuid4())
    trigger_dir = pathlib.Path(config.trigger_dir())
    trigger_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": trigger_id, "type": "force_osint_scrape", "payload": {}}
    (trigger_dir / f"{trigger_id}.json").write_text(json.dumps(payload))
    db.insert_trigger(trigger_id, "force_osint_scrape", {})
    return JSONResponse({"trigger_id": trigger_id, "status": "accepted"}, status_code=202)


@app.post("/admin/push-alert")
@app.post("/admin/push-test-alert")  # legacy alias
async def push_alert(
    body: TestAlertRequest,
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    """Send an ntfy push to any topic. NOT idempotent — each POST sends a separate push.
    Body: { message, topic (default: ops-health), title, priority (1-5, default: 3) }
    """
    if len(body.message) > 200:
        raise HTTPException(status_code=400, detail="Message max 200 chars")

    trigger_id = str(uuid.uuid4())
    trigger_dir = pathlib.Path(config.trigger_dir())
    trigger_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": trigger_id, "type": "push_test_alert",
               "payload": {"message": body.message, "topic": body.topic,
                           "title": body.title, "priority": body.priority}}
    (trigger_dir / f"{trigger_id}.json").write_text(json.dumps(payload))
    db.insert_trigger(trigger_id, "push_test_alert",
                      {"message": body.message, "topic": body.topic})

    return JSONResponse(
        {"trigger_id": trigger_id, "status": "accepted"},
        status_code=202,
    )


# ── VIP watchlist ──────────────────────────────────────────────────────────────

def _read_vip_list() -> list[str]:
    path = pathlib.Path(config.vip_watchlist_path())
    if not path.exists():
        return []
    return [
        line.strip().upper()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _write_vip_list(entries: list[str]) -> None:
    path = pathlib.Path(config.vip_watchlist_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sorted(set(entries))) + "\n")


@app.get("/admin/vip")
async def get_vip(tier: Tier = Depends(require_admin)) -> JSONResponse:
    return JSONResponse({"vip": _read_vip_list()})


class VIPAddRequest(BaseModel):
    entry: str


@app.post("/admin/vip")
async def add_vip(
    body: VIPAddRequest,
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    entry = body.entry.strip().upper()
    if not entry:
        raise HTTPException(status_code=400, detail="Empty entry")
    current = _read_vip_list()
    if entry not in current:
        current.append(entry)
        _write_vip_list(current)
    return JSONResponse({"vip": sorted(set(current)), "added": entry})


@app.delete("/admin/vip/{entry}")
async def delete_vip(
    entry: str,
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    entry = entry.strip().upper()
    current = _read_vip_list()
    if entry not in current:
        raise HTTPException(status_code=404, detail=f"{entry!r} not in VIP list")
    current = [e for e in current if e != entry]
    _write_vip_list(current)
    return JSONResponse({"vip": sorted(set(current)), "removed": entry})
