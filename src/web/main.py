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
  POST /admin/push-test-alert          Admin
  GET  /admin/vip                      Admin
  POST /admin/vip                      Admin
  DELETE /admin/vip/{entry}            Admin
"""

import json
import pathlib
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
    }
    # REST feeds that are covered by a push source — skip staleness check when push is healthy.
    push_covers = {"nws": "push:nws"}
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
        "push:nws": 300, "push:fdps": 300, "push:stdds": 300,
        "push:fns": 300, "push:itws": 300,
        "push:amtrak": 300,
    }
    # REST feeds covered by a push source — stale REST is expected when push is live.
    push_covers: dict[str, str] = {"nws": "push:nws"}
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
async def get_brief_history(limit: int = 7) -> JSONResponse:
    """Return metadata for the last `limit` briefs (default 7). Tier 0."""
    entries = db.get_brief_history(min(max(limit, 1), 30))
    return JSONResponse(entries)


@app.get("/api/v1/brief/{brief_id}")
async def get_brief_by_id(brief_id: int) -> PlainTextResponse:
    """Return the full content of an archived brief by ID. Tier 0."""
    row = db.get_brief_by_id(brief_id)
    if not row:
        return PlainTextResponse("Brief not found.", status_code=404)
    return PlainTextResponse(row["content"])


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
        return JSONResponse({"available": False, "summary": "No data yet"})
    return JSONResponse({
        "available": True,
        "summary": status["delay_summary"],
        "fetched_at": status["fetched_at"],
    })


    return JSONResponse({
        "available": True,
        "plan_date": plan["plan_date"],
        "trip_count": plan["trip_count"],
        "trips": _json.loads(plan["raw_json"]).get("trips", []),
        "loaded_at": plan["loaded_at"],
    })


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


@app.post("/admin/push-test-alert")
async def push_test_alert(
    body: TestAlertRequest,
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    """NOT idempotent — each POST sends a separate ntfy push."""
    if len(body.message) > 200:
        raise HTTPException(status_code=400, detail="Message max 200 chars")

    trigger_id = str(uuid.uuid4())
    trigger_dir = pathlib.Path(config.trigger_dir())
    trigger_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": trigger_id, "type": "push_test_alert",
               "payload": {"message": body.message}}
    (trigger_dir / f"{trigger_id}.json").write_text(json.dumps(payload))
    db.insert_trigger(trigger_id, "push_test_alert", {"message": body.message})

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
