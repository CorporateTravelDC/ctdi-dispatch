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
  GET  /api/v1/adsb                    Tier 0 — global ADS-B (airplanes.live proxy; ?lat=&lon=&radius= params)

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

import html
import json
import os
import pathlib
import posixpath
import sqlite3
import time
import httpx
import uuid
from typing import Optional
from urllib.parse import unquote

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from auth.auth import Tier, require_admin, require_tier, resolve_tier, resolve_identity
from common import config, db
import secrets as _secrets
from second_brain.scrub_gate import ScrubGateBlocked as _ScrubGateBlocked, gate as _scrub_gate
from web.routes.watchlist import router as watchlist_router
from web.routes.fids import router as fids_router
from web.routes.airspace import router as airspace_router
from web.routes.data_usage import router as data_usage_router
from web.routes.webhooks import router as webhooks_router
from web.routes.sectors import router as sectors_router
from web.routes.remember import router as remember_router
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
app.include_router(data_usage_router)
app.include_router(webhooks_router)
app.include_router(sectors_router)
app.include_router(remember_router)

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
    db.init_db_v13()
    db.init_db_v14()
    db.init_db_v15()
    db.init_db_v16()
    db.init_db_v18()
    db.init_db_v19()
    db.init_db_v20()
    db.init_db_v21()
    db.init_db_v22()
    db.init_db_v23()
    db.init_db_v24()
    db.init_db_v25()
    db.init_db_v26()
    db.init_db_v27()
    db.init_db_v28()
    db.init_db_v29()
    db.init_db_v30()
    db.init_db_v31()
    db.init_db_v32()
    db.init_db_v33()


# ── Tier 0 — Public (Cloudflare Tunnel + Tailscale) ───────────────────────────

@app.get("/api/v1/whoami-token")
async def whoami_token(identity: dict = Depends(resolve_identity)) -> JSONResponse:
    """
    Added 2026-08-02 for the department/multi-operator RSS feed visibility
    model. Lets a caller (currently: the runner service, which never
    touches the shared DB directly) resolve a Bearer token to
    {tier, user_label, department, token_prefix} without needing its own
    DB connection or token-hashing logic -- runner just forwards whatever
    Authorization header it received and trusts this response. Tier 0 --
    an invalid/missing token isn't an error here, it just resolves to
    anonymous (tier="tier0", the rest null), same as resolve_tier()
    everywhere else in this codebase never raises for anonymous callers.
    """
    return JSONResponse(identity)


# ── Cowork <-> Dispatch message board (Tier-0, tunnel-reachable) ─────────────
# Two-way coordination channel. The Cloudflare tunnel STRIPS the Authorization
# header, so board writes authenticate with a custom X-Board-Key header (which
# the tunnel does NOT strip), NOT the Bearer/tier system. Reads are anonymous
# Tier-0. Every POST runs the same CUI/PII scrub gate as /api/v1/remember.
# Coordination text only -- substantive payloads live in the vault, referenced
# via `refs`. See db.board_* and the build contract.
_BOARD_KEY = os.getenv("BOARD_KEY", "").strip()
_board_post_hits: list = []   # naive in-memory rate-limit clock

# 2026-08-16: Tier-0 vault research reads (see vault_research_read below) --
# for agent tools that structurally cannot send an Authorization header or
# carry credentials in a URL (Cowork's fetch tool; confirmed live the same
# night board-write auth hit the identical wall).
#
# 2026-08-16 (widened): originally just 01-Sources/personal-notes/<topic>/,
# topic-pattern-matched ("Research - X" / "X Series"). Widened twice since:
#
# (a) broader "processed/synthesized second-brain output" surface, so Cowork
#     can self-serve research without the operator manually relaying every finding.
#     Each addition was checked against what actually writes there, not
#     assumed:
#       - 04-Syntheses/       -- daily/weekly digest output; every poller
#                                skill writing here imports
#                                second_brain.scrub_gate (aam_daily_watch.py,
#                                second_brain_weekly.py, etc.) -- scrub-gated
#                                at write time.
#       - 02-Concepts/         -- distilled concept notes.
#       - 00-Inbox/cross-link-findings/ -- entity_tracking.py's novel
#                                pattern-recognition output, via the pinned
#                                osint-monitor model. Not the rest of
#                                00-Inbox -- rss/ and personal-research/ stay
#                                out (raw/pre-triage).
#
# (b) replaced the per-topic naming-pattern check under personal-notes/ with
#     one PARENT folder, 01-Sources/personal-notes/Series/, covered
#     recursively -- any topic folder the operator drops under it (Uber Series/,
#     Family Office - CTDI/, anything future) is automatically in scope with
#     zero further code changes. Vault-side move executed same night: old
#     Uber Series/ -> Series/Uber Series/ (verified byte-identical before
#     the old copy was removed), duplicate Research - Uber Series/ retired
#     (was a byte-identical mirror of Uber Series/, confirmed via
#     content-length + write-timestamp comparison, nothing unique lost),
#     Family Office - CTDI/ created fresh under the new parent (previously
#     existed outside any scope entirely). Sibling topic folders NOT under
#     Series/ are still out of scope -- moving into Series/ is what puts a
#     topic in reach, not the name alone.
#
# Deliberately EXCLUDED, checked and rejected, not just unconsidered:
#   - Docs/                -- contains pentest logs (LIVE_PENTEST_*),
#                            compliance reviews, investor-materials/. Real
#                            finding from this same review pass -- almost
#                            got included by pattern-matching alone.
#   - 06-AI-Memory/         -- the on-device notepad; explicitly established
#                            (2026-08-11 board thread) as Pi-only, never
#                            meant to reach Cowork's side.
#   - Contacts/, 03-Entities/, .internal-backups/, archives/, 99-Archive/,
#     01-Sources/{daily,manual,rss,transport-patterns}/ -- raw/pre-synthesis
#     sources or PII-adjacent, not vetted for unauthenticated exposure.
# Same CUI/PII scrub gate as before applies to every read regardless of
# which allowed prefix it came from -- none of this widening weakens that.
_VAULT_RESEARCH_ROOT = "01-Sources/personal-notes/Series"
_VAULT_RESEARCH_EXTRA_PREFIXES = (
    "04-Syntheses/",
    "02-Concepts/",
    "00-Inbox/cross-link-findings/",
)
_vault_research_hits: list = []   # naive in-memory rate-limit clock


def _vault_research_path_allowed(path: str) -> bool:
    if path.startswith(_VAULT_RESEARCH_ROOT + "/"):
        return True
    return any(path.startswith(p) for p in _VAULT_RESEARCH_EXTRA_PREFIXES)


def _vault_path_is_safe(path: str) -> bool:
    """Traversal guard for every vault path served to a client.

    2026-08-16 drift audit: the old inline check was `".." in path or
    path.startswith("/")`. Starlette percent-decodes a query value exactly
    ONCE, so a plain `..` / single-encoded `%2e%2e` is caught -- but a
    DOUBLE-encoded `%252e%252e` arrives here as the literal `%2e%2e`
    (no `..`), passes, and is then handed to webdav_client.get() ->
    requests, whose requote_uri() does quote(unquote(...)) and decodes it
    back to a real `../` before it hits the Nextcloud/WebDAV backend. That
    is the ONLY app-layer traversal defense on these routes (two of them
    are Tier-0/unauthenticated), so it must survive multi-round decoding.

    Fix: fully decode (loop until stable, defeating N-times encoding), then
    re-assert no `..`, leading `/`, or backslash survives, and normalize +
    re-check. Non-breaking: only rejects paths that DECODE to a traversal;
    ordinary vault paths (segment names, `/`, spaces, hyphens) pass.
    """
    if not path or path.startswith("/"):
        return False
    decoded = path
    for _ in range(5):
        nxt = unquote(decoded)
        if nxt == decoded:
            break
        decoded = nxt
    if ".." in decoded or decoded.startswith("/") or "\\" in decoded:
        return False
    norm = posixpath.normpath(decoded)
    if norm == ".." or norm.startswith("../") or norm.startswith("/"):
        return False
    return True


class BoardMsgIn(BaseModel):
    from_: str = Field(alias="from")
    to: str
    thread: str
    subject: str
    body: str
    refs: Optional[list] = None
    in_reply_to: Optional[str] = None


@app.get("/api/v1/board/health")
async def board_health() -> JSONResponse:
    """Board reachability probe -- Tier 0, no auth."""
    return JSONResponse({"status": "ok"})


@app.get("/api/v1/board")
async def board_get(thread: str = "coord", since: str = "", limit: int = 50) -> JSONResponse:
    """Read board messages (Tier-0/anonymous). since = opaque cursor from a prior
    read (seq or ISO ts); returns only newer messages plus a fresh cursor."""
    msgs, cursor = db.board_query(thread=thread, since=since or None, limit=limit)
    return JSONResponse({"messages": msgs, "cursor": cursor})


@app.get("/api/v1/board/threads")
async def board_threads() -> JSONResponse:
    """List board threads + last-activity ts -- Tier 0."""
    return JSONResponse({"threads": db.board_threads()})


@app.get("/api/v1/board/enroll")
async def board_enroll(nonce: str = "") -> JSONResponse:
    """One-time enrollment fetch -- Tier-0, no Authorization (the CF tunnel
    strips it). A valid, unconsumed, unexpired nonce returns a short-lived
    board-write TOKEN exactly once, then the nonce is dead. Reads never need a
    key, so this only enables the caller's writes. The enroll URL is not itself
    the secret -- consuming it once is what returns the secret.

    200 -> {token, expires_at, scope}; 401 -> unknown nonce; 410 -> nonce
    already consumed or expired (replay/leak of the URL is worthless)."""
    if not nonce:
        raise HTTPException(status_code=400, detail="nonce query param is required")
    r = db.board_consume_nonce(nonce)
    st = r["status"]
    if st == "ok":
        return JSONResponse({
            "token": r["token"],
            "expires_at": r["expires_at"],
            "scope": r["scope"],
            "usage": "send this value as the X-Board-Key header on POST /api/v1/board",
        })
    if st == "invalid":
        raise HTTPException(status_code=401, detail="invalid enrollment nonce")
    raise HTTPException(status_code=410, detail=f"enrollment nonce {st} (single-use, ~10min TTL)")


@app.get("/api/v1/board/refresh")
async def board_refresh(request: Request) -> JSONResponse:
    """Self-rotate a still-valid board-write token (X-Board-Key header) into a
    fresh one -- autonomous day-to-day, no human step required. GET, not
    POST -- same GET-only-tool-compatible design as /enroll.

    Gated on a weekly GPG-clearsigned human presence attestation (see
    scripts/board-presence-attest.sh): once that 7-day window lapses, refresh
    fails closed (403) regardless of the presented token's own validity, and
    a fresh human-issued enrollment (GET /api/v1/board/enroll) is required to
    start a new cycle.

    200 -> {token, expires_at, scope}; 401 -> presented token missing/invalid/
    expired; 403 -> presence attestation stale or missing."""
    presented = request.headers.get("X-Board-Key", "")
    r = db.board_refresh_token(
        presented, remote_addr=(request.client.host if request.client else None)
    )
    if r["status"] == "invalid_token":
        raise HTTPException(status_code=401, detail="missing or invalid/expired X-Board-Key")
    if r["status"] == "presence_stale":
        raise HTTPException(
            status_code=403,
            detail=(
                f"presence attestation stale or missing (valid_until="
                f"{r.get('presence_valid_until')}) -- requires a fresh human-run "
                f"scripts/board-presence-attest.sh, then a new enrollment"
            ),
        )
    return JSONResponse({
        "token": r["token"], "expires_at": r["expires_at"], "scope": r["scope"],
        "usage": "send this value as the X-Board-Key header on POST /api/v1/board "
                 "or this refresh endpoint",
    })


@app.post("/api/v1/board", status_code=201)
async def board_post(msg: BoardMsgIn, request: Request) -> JSONResponse:
    """Post a board message. Authenticates via X-Board-Key (NOT Authorization --
    the CF tunnel strips Authorization). 401 on bad/missing key; 422 if the
    CUI/PII scrub gate blocks (do not retry same text); 201 on success."""
    # Authorize via X-Board-Key: either the long-lived master BOARD_KEY OR a
    # short-lived board-write token minted through /api/v1/board/enroll.
    presented = request.headers.get("X-Board-Key", "")
    authorized = bool(presented) and (
        (bool(_BOARD_KEY) and _secrets.compare_digest(presented, _BOARD_KEY))
        or db.board_token_valid(presented)
    )
    if not authorized:
        raise HTTPException(status_code=401, detail="missing or invalid X-Board-Key")
    now = time.monotonic()
    _board_post_hits[:] = [t for t in _board_post_hits if now - t < 60]
    if len(_board_post_hits) >= 30:
        raise HTTPException(status_code=429, detail="board POST rate limit (30/min) exceeded")
    _board_post_hits.append(now)
    try:
        _scrub_gate("\n".join([msg.subject or "", msg.body or "", " ".join(msg.refs or [])]),
                    source="board")
    except _ScrubGateBlocked as e:
        raise HTTPException(status_code=422, detail=f"blocked by CUI/PII scrub gate: {e}")
    rec = db.board_insert(
        from_side=msg.from_, to_side=msg.to, thread=msg.thread,
        subject=msg.subject, body=msg.body, refs=msg.refs, in_reply_to=msg.in_reply_to,
        remote_addr=(request.client.host if request.client else None),
    )
    return JSONResponse({"id": rec["id"], "ts": rec["ts"]}, status_code=201)


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
        # 2026-08-10: was 180 -- tighter than the actual 300s poll interval
        # (poller/main.py's FETCHERS list), guaranteeing "stale" for the last
        # ~2 min of every 5-min cycle by construction, not a real degradation.
        # 600 = 2x the real interval, matching every other REST feed's
        # threshold convention in this same dict.
        "dca_fids": 600, "iad_fids": 600,
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
        # 2026-08-10: was 180 -- tighter than the actual 300s poll interval
        # (poller/main.py's FETCHERS list), guaranteeing "stale" for the last
        # ~2 min of every 5-min cycle by construction, not a real degradation.
        # 600 = 2x the real interval, matching every other REST feed's
        # threshold convention in this same dict. Also removed a pre-existing
        # duplicate-key entry for these same two feeds later in this same
        # dict literal (harmless while both copies agreed on 180, but a
        # silent footgun -- Python dict literals let a later duplicate key
        # win with no warning, so editing the wrong copy would have done
        # nothing).
        "dca_fids": 600, "iad_fids": 600,
        "push:nws": 300, "push:fdps": 300, "push:stdds": 300,
        "push:fns": 300, "push:itws": 300,
        "push:amtrak": 300,
    }
    # REST feeds covered by a push source — stale REST is expected when push is live.
    # "tfr": "push:stdds" and "nas": "push:tfms" removed 2026-07-23 -- same bogus
    # 2026-06-07 POC mapping fixed in poller/main.py's FETCH_SCHEDULE (STDDS/TFMS
    # are unrelated-to-partially-overlapping feeds, not real push sources for
    # tfr/nas). This dict was making /api/v1/feeds report push_covered=true for
    # both, hiding 2+ days of real staleness on the admin/overview dashboards
    # while the underlying REST polls were also being silently skipped.
    push_covers: dict[str, str] = {"nws": "push:nws", "notam": "push:fns"}
    feed_by_name = {f["feed_name"]: f for f in feeds}

    # Belt-and-suspenders pull-path viability (poller/skills/pull_path_verify.py,
    # 12h timer). Independent of push freshness -- confirms the pull FALLBACK
    # endpoint still works even when a push source is what's carrying the data.
    pull_status = db.get_pull_path_status()
    # Push feeds whose fallback is a pull source of a different name -- so the
    # push row can also show "pull: verified" for its own fallback path.
    push_to_pull = {"push:nws": "nws", "push:fns": "notam", "push:amtrak": "amtrak"}

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

        # When a push source is actively covering this feed, the pull-side's
        # own error (e.g. "awaiting_credentials") is real but not actionable
        # or alarming -- the feed IS healthy, just not via the REST path.
        # Added 2026-08-02 after this showed notam as simultaneously
        # push_covered=true and error="awaiting_credentials", which reads
        # as "broken" even though 267 facilities' worth of real NOTAMs were
        # updating live via push the whole time. `error` now reflects actual
        # feed health (null when push-covered); the raw pull-side detail is
        # preserved separately in `pull_error` rather than dropped.
        pull_error = f["error"]
        display_error = None if push_covered else pull_error

        result.append({
            "feed_name":              name,
            "fetched_at":             f["fetched_at"],
            "age_seconds":            age,
            "stale_threshold_seconds": threshold,
            "push_covered":           push_covered,
            "error":                  display_error,
            "pull_error":             pull_error,
            "consecutive_failures":   f["consecutive_failures"],
            # Belt-and-suspenders pull-path viability (12h probe), independent
            # of freshness. None = no probe defined for this feed.
            "pull_verified":          ((None if pp["ok"] is None else bool(pp["ok"])) if (pp := (pull_status.get(name) or pull_status.get(push_to_pull.get(name, "")))) else None),
            "pull_state":             (pp["state"] if pp else None),
            "pull_checked_at":        (pp["checked_at"] if pp else None),
            "pull_detail":            (pp["detail"] if pp else None),
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


@app.get("/api/v1/flightplan/{callsign}")
async def get_flight_plan(callsign: str) -> JSONResponse:
    """Confirmed flight-plan details from FAA FDPS (SWIM/SFDPS FIXM feed) —
    Tier 0. Filed origin/destination/aircraft type/status, straight from the
    FAA's own flight plan message for this callsign -- not inferred from
    ADS-B position or scraped from an airport FIDS page. Coverage is whatever
    the live FDPS feed has carried; a miss here doesn't mean the flight
    doesn't exist, just that FDPS hasn't (yet) had a matching flight plan
    message for it.
    """
    plan = db.get_flight_plan_by_callsign(callsign)
    if not plan:
        return JSONResponse({
            "callsign": callsign.strip().upper(),
            "confirmed": False,
            "source": "fdps",
            "reason": "No FDPS flight plan on file for this callsign",
        })
    return JSONResponse({
        "callsign": callsign.strip().upper(),
        "confirmed": True,
        "source": "fdps",
        "origin": plan.get("origin"),
        "destination": plan.get("destination"),
        "aircraft_type": plan.get("aircraft_type"),
        "status": plan.get("status"),
        "departure_time": plan.get("departure_time"),
        "arrival_time": plan.get("arrival_time"),
        "updated_at": plan.get("updated_at"),
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


@app.get("/api/v1/wx-config")
async def get_wx_config() -> JSONResponse:
    """
    Operator meteorology config -- Tier 0.

    Returns the NWS (default) radar source, centered on the operator's local
    forecast office, plus an optional operator-defined alternate source for
    non-US deployments (a different national met office, or any embeddable
    radar/satellite image or iframe URL). WX_OPERATOR_* unset -> the
    frontend hides the toggle and shows NWS only.

    Config (dispatch.env):
      WX_NWS_RADAR_SITE   -- 4-letter NWS radar site ID (default KLWX --
                              Sterling, VA WFO, covers the DC metro area)
      WX_NWS_WFO          -- 3-letter NWS Weather Forecast Office ID, for
                              the "local forecast office" label (default LWX)
      WX_OPERATOR_NAME    -- display name for the alternate source (e.g.
                              "Met Office", "Environment Canada")
      WX_OPERATOR_MAP_URL -- embeddable image or iframe URL for the
                              alternate source's local radar/satellite view
      WX_OPERATOR_IS_IFRAME -- "true" if WX_OPERATOR_MAP_URL should be
                              embedded as an <iframe> rather than an <img>
                              (some met offices only offer an interactive
                              map page, not a static/animated image)
    """
    nws_site = config.get("WX_NWS_RADAR_SITE", "KLWX").strip().upper() or "KLWX"
    nws_wfo  = config.get("WX_NWS_WFO", "LWX").strip().upper() or "LWX"
    # Display label for the default radar source -- "NEXRAD" (the actual
    # radar network/product name for the /ridge/standard/ base-reflectivity
    # loop) rather than "NWS" (the parent agency), per operator request
    # 2026-07-21. The underlying default site/office (Sterling/LWX) is
    # unaffected -- this only renames the toggle button / panel label.
    nws_label = config.get("WX_NWS_LABEL", "NEXRAD").strip() or "NEXRAD"

    operator_name = config.get("WX_OPERATOR_NAME", "").strip()
    operator_url  = config.get("WX_OPERATOR_MAP_URL", "").strip()
    operator_iframe = config.get("WX_OPERATOR_IS_IFRAME", "").strip().lower() == "true"

    operator = None
    if operator_name and operator_url:
        operator = {
            "name":       operator_name,
            "map_url":    operator_url,
            "is_iframe":  operator_iframe,
        }

    return JSONResponse({
        "nws": {
            "name":     nws_label,
            "wfo":      nws_wfo,
            "radar_site": nws_site,
            "radar_url":  f"https://radar.weather.gov/ridge/standard/{nws_site}_loop.gif",
            # National composite loop -- added 2026-08-03, same
            # radar.weather.gov path family as the site loop above, just
            # "CONUS" instead of the per-deployment site code. Verified
            # live: 200/real image/gif, ~650KB (picked over the ~6.6MB
            # CONUS-LARGE variant to keep the 150s poll-driven cache-bust
            # from hammering bandwidth).
            "radar_url_conus": "https://radar.weather.gov/ridge/standard/CONUS_loop.gif",
            "station_page": f"https://radar.weather.gov/station/{nws_site.lower()}/standard",
        },
        "operator": operator,
        # WPC national surface prog series -- added 2026-08-03. Public NOAA
        # imagery, no key/auth, same direct-image-link pattern as the NEXRAD
        # loop above (radar.weather.gov). Verified live: all 9 URLs return
        # 200/real JPEGs. Static/hardcoded here rather than config-driven
        # since these are fixed, WPC-documented product URLs, not a
        # per-deployment operator choice like the NWS/operator radar toggle.
        "prog": {
            "name": "WPC SFC PROG",
            "current": {
                "label": "Current Analysis",
                "url": "https://www.wpc.ncep.noaa.gov/sfc/namussfcwbg.jpg",
            },
            "forecasts": [
                {"hour": 6,  "label": "6HR",  "url": "https://www.wpc.ncep.noaa.gov/basicwx/91fndfd.jpg"},
                {"hour": 12, "label": "12HR", "url": "https://www.wpc.ncep.noaa.gov/basicwx/92fndfd.jpg"},
                {"hour": 18, "label": "18HR", "url": "https://www.wpc.ncep.noaa.gov/basicwx/93fndfd.jpg"},
                {"hour": 24, "label": "24HR", "url": "https://www.wpc.ncep.noaa.gov/basicwx/94fndfd.jpg"},
                {"hour": 30, "label": "30HR", "url": "https://www.wpc.ncep.noaa.gov/basicwx/95fndfd.jpg"},
                {"hour": 36, "label": "36HR", "url": "https://www.wpc.ncep.noaa.gov/basicwx/96fndfd.jpg"},
                {"hour": 48, "label": "48HR", "url": "https://www.wpc.ncep.noaa.gov/basicwx/98fndfd.jpg"},
                {"hour": 60, "label": "60HR", "url": "https://www.wpc.ncep.noaa.gov/basicwx/99fndfd.jpg"},
            ],
        },
        # 2026-08-03 (later same day): expanded from Western Atlantic-only to
        # cover the entire US -- the operator's ask: "the Maritime equivalent for
        # the entire U.S. ... Gulf Shore ... Eastern Pacific". Added NOAA's
        # joint Unified Surface Analysis (one chart, whole CONUS + Gulf +
        # both ocean approaches) plus a full Eastern Pacific SFC/WAVE
        # NOW/24H/48H set mirroring the existing Western Atlantic set,
        # sourced from OPC's ocean.weather.gov (same fixed-filename static
        # image pattern as tgftp.nws.noaa.gov/fax/, just a different NOAA
        # host). Existing WATL charts kept, only relabeled (WATL prefix)
        # now that other regions coexist in the same toggle row.
        "maritime": {
            "name": "OPC MARITIME",
            "charts": [
                {"key": "watl-sfc-current",  "label": "WATL SFC NOW",  "url": "https://tgftp.nws.noaa.gov/fax/PYAD10.gif"},
                {"key": "watl-sfc-24",       "label": "WATL SFC 24H",  "url": "https://tgftp.nws.noaa.gov/fax/PPAE10.gif"},
                {"key": "watl-sfc-48",       "label": "WATL SFC 48H",  "url": "https://tgftp.nws.noaa.gov/fax/QDTM10.gif"},
                {"key": "watl-wave-current", "label": "WATL WAVE NOW", "url": "https://tgftp.nws.noaa.gov/fax/PWAA90.gif"},
                {"key": "watl-wave-24",      "label": "WATL WAVE 24H", "url": "https://tgftp.nws.noaa.gov/fax/PWAE10.gif"},
                {"key": "watl-wave-48",      "label": "WATL WAVE 48H", "url": "https://tgftp.nws.noaa.gov/fax/PJAI10.gif"},
                {"key": "unified-us",        "label": "UNIFIED (US)",  "url": "https://ocean.weather.gov/UA/entire_UA.gif"},
                {"key": "epac-sfc-current",  "label": "EPAC SFC NOW",  "url": "https://ocean.weather.gov/shtml/P_full_00hrsfc.gif"},
                {"key": "epac-sfc-24",       "label": "EPAC SFC 24H",  "url": "https://ocean.weather.gov/shtml/P_24hrsfc.gif"},
                {"key": "epac-sfc-48",       "label": "EPAC SFC 48H",  "url": "https://ocean.weather.gov/shtml/P_48hrsfc.gif"},
                {"key": "epac-wave-current", "label": "EPAC WAVE NOW", "url": "https://ocean.weather.gov/shtml/P_00hrww.gif"},
                {"key": "epac-wave-24",      "label": "EPAC WAVE 24H", "url": "https://ocean.weather.gov/shtml/P_24hrww.gif"},
                {"key": "epac-wave-48",      "label": "EPAC WAVE 48H", "url": "https://ocean.weather.gov/shtml/P_48hrww.gif"},
            ],
        },
    })



# ── AIRMET/SIGMET hazard overlay -----------------------------------------
# Added 2026-08-03. Aviation hazard polygons (icing, turbulence, IFR,
# mountain obscuration, convective) for the MapView overlay layer.
#
# Source: AWC's real Data API (aviationweather.gov/api/data/airsigmet),
# NOT the aviationweather.gov HTML/progchart pages -- those are blocked by
# an edge WAF for automated requests (confirmed live, "the request is
# blocked" on every attempt, both from this box and from an unrelated
# network). The /api/data/* endpoints are the same, unblocked, documented
# public API already used elsewhere on this platform for METAR/TAF.
# airsigmet covers BOTH domestic AIRMET and SIGMET products in one call
# (airSigmetType field distinguishes them) -- no need for the separate
# gairmet/isigmet endpoints; isigmet in particular is international/
# oceanic SIGMETs, not relevant to DC-area ops.
#
# Real data, live-fetched, in both live and demo mode this cycle -- same
# "not replay-captured yet" approach as the wx-config prog/maritime charts
# above. Server-side 5-minute cache to avoid hammering AWC on every client
# poll (the map layer refreshes every 5 min client-side too).

_airmets_cache: dict = {"fetched_at": 0.0, "data": []}
_AIRMETS_CACHE_TTL = 300  # seconds

_HAZARD_COLOR = {
    "CONVECTIVE": "#ff3131",
    "TURB":       "#a855f7",
    "ICE":        "#4a9eff",
    "IFR":        "#ffd700",
    "MTN_OBSCN":  "#8b6f47",
    "LLWS":       "#ff8c00",
    "SFC_WND":    "#ff8c00",
}


def _normalize_airsigmet(r: dict) -> dict | None:
    coords = r.get("coords") or []
    latlngs = [[c["lat"], c["lon"]] for c in coords if "lat" in c and "lon" in c]
    if len(latlngs) < 3:
        return None
    hazard = (r.get("hazard") or "").upper().replace(" ", "_")
    return {
        "id": f"{r.get('icaoId','')}-{r.get('seriesId','')}-{r.get('alphaChar','')}",
        "type": r.get("airSigmetType") or "AIRMET",
        "hazard": hazard,
        "color": _HAZARD_COLOR.get(hazard, "#9ca3af"),
        "severity": r.get("severity"),
        "altitude_low": r.get("altitudeLow1"),
        "altitude_high": r.get("altitudeHi1"),
        "valid_from": r.get("validTimeFrom"),
        "valid_to": r.get("validTimeTo"),
        "coords": latlngs,
        "raw_text": (r.get("rawAirSigmet") or "")[:600],
    }


async def _fetch_airmets() -> list:
    import time as _time
    now = _time.time()
    if now - _airmets_cache["fetched_at"] < _AIRMETS_CACHE_TTL and _airmets_cache["data"]:
        return _airmets_cache["data"]
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get("https://aviationweather.gov/api/data/airsigmet",
                             params={"format": "json"}, timeout=15)
            r.raise_for_status()
            raw = r.json()
        normalized = [n for n in (_normalize_airsigmet(x) for x in raw) if n]
        _airmets_cache["data"] = normalized
        _airmets_cache["fetched_at"] = now
        return normalized
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning("airmets: AWC fetch failed: %s", e)
        return _airmets_cache["data"]  # serve stale cache rather than empty on a transient failure


@app.get("/api/v1/airmets")
async def get_airmets() -> JSONResponse:
    """Active AIRMET/SIGMET hazard polygons — Tier 0. Public FAA/AWC data."""
    data = await _fetch_airmets()
    return JSONResponse({"airmets": data, "count": len(data)})


@app.get("/api/v1/bandwidth-priority")
async def get_bandwidth_priority_route() -> JSONResponse:
    """
    Current bandwidth-priority override state — Tier 0 (no auth), so any
    container on the box (ingest, a future NEXRAD puller) can poll it
    without needing an admin token. See /admin/bandwidth-priority to set it.
    """
    return JSONResponse(db.get_bandwidth_priority())


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
    # 2026-08-12 (SCHEMA_V32) -- only meaningful for scope_type="event",
    # ignored otherwise. See osint_monitor.py's _EVENT_SCOPE_TYPES.
    event_name:     str = ""
    audience:       str = ""
    genre:          str = ""


@app.get("/api/v1/osint/feed")
async def osint_feed(
    scope_id: Optional[int] = Query(default=None),
    min_score: int = Query(default=0, ge=0, le=10),
    limit: int = Query(default=50, le=200),
) -> JSONResponse:
    """Recent OSINT items, newest first. Filter by scope_id and/or min_score."""
    items = db.osint_get_feed(scope_id=scope_id, min_score=min_score, limit=limit)

    # 2026-08-12: cross-outlet story clustering -- annotate each item with
    # how many distinct outlets in THIS returned batch share its story_key
    # (see osint_monitor._story_key). Computed here rather than in SQL so
    # it stays correct regardless of scope/min_score filtering; the PWA
    # uses crossover_count to group same-story items instead of listing
    # what looks like duplicate rows.
    story_outlets: dict[str, set] = {}
    for it in items:
        sk = it.get("story_key")
        if sk:
            story_outlets.setdefault(sk, set()).add(it.get("outlet") or it.get("source_name") or "")
    for it in items:
        sk = it.get("story_key")
        it["crossover_count"] = len(story_outlets[sk]) if sk else 1

    return JSONResponse({"items": items, "count": len(items)})


# 2026-08-12: second-brain knowledge graph (src/second_brain/knowledge_graph/
# build_graph.py). Served from the shared data volume, NOT the repo path
# baked into this container's image at build time -- see that module's
# main() for why (a container rebuild shouldn't be required just to see a
# freshly-regenerated graph).
_KG_LIVE_DIR = "/var/lib/corporatetraveldc/knowledge_graph"


@app.get("/api/v1/knowledge-graph/html", response_class=HTMLResponse)
async def knowledge_graph_html(
    tier: Tier = Depends(require_tier(Tier.T1)),
) -> HTMLResponse:
    """Self-contained interactive vault knowledge-graph viz (canvas-rendered,
    no external assets). Iframed by the PWA's Graph tab.

    2026-08-13: tier-gated after a live pentest pass found this endpoint
    (and knowledge_graph_meta/vault_file below) had NO auth check at all --
    every other endpoint touching anything sensitive in this file uses
    require_tier/require_admin, these three didn't. T1 == Tailscale-origin
    per auth.py, so this closes public-internet exposure of the full
    second-brain vault without affecting real (Tailscale) operator use.
    Deliberately NOT added to runner/main.py's _TIER1_PATHS service-token
    injection list -- this is an internal operator tool, not something to
    widen to the public/Ops view the way tfr-enriched/radio/watchlist are.
    """
    path = os.path.join(_KG_LIVE_DIR, "vault-graph.html")
    if not os.path.exists(path):
        raise HTTPException(
            404,
            "Graph not built yet -- run: python3 -m "
            "second_brain.knowledge_graph.build_graph",
        )
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/v1/knowledge-graph/meta")
async def knowledge_graph_meta(
    tier: Tier = Depends(require_tier(Tier.T1)),
) -> JSONResponse:
    """Just the meta block (node/edge counts, generated_at) -- cheap enough
    to poll for a "graph last built at ..." indicator without pulling the
    full payload. Tier-gated -- see knowledge_graph_html."""
    path = os.path.join(_KG_LIVE_DIR, "graph.json")
    if not os.path.exists(path):
        raise HTTPException(404, "Graph not built yet")
    with open(path, encoding="utf-8") as f:
        graph = json.load(f)
    return JSONResponse(graph.get("meta", {}))


_VAULT_FILE_PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light; --bg: #fcfcfb; --fg: #0b0b0b; --muted: #77756f; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ color-scheme: dark; --bg: #1a1a19; --fg: #ffffff; --muted: #8f8d85; }}
  }}
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ background: var(--bg); color: var(--fg); }}
  header {{ padding: 10px 14px; font: 12px/1.4 system-ui, sans-serif; color: var(--muted);
            border-bottom: 1px solid var(--muted); }}
  pre {{ margin: 0; padding: 14px; white-space: pre-wrap; word-break: break-word;
         font: 13px/1.5 ui-monospace, "SF Mono", Consolas, monospace; }}
</style>
<header>{path}</header>
<pre>{body}</pre>
"""


@app.get("/api/v1/vault/file")
async def vault_file(
    path: str = Query(...),
    tier: Tier = Depends(require_tier(Tier.T1)),
) -> HTMLResponse:
    """Serve one vault file's raw content via a server-side authenticated
    WebDAV GET. Backs the knowledge-graph viz's "open file" links.
    Tier-gated -- see knowledge_graph_html.

    2026-08-12: the obvious alternative -- linking straight to Nextcloud's
    own WebDAV URL -- doesn't work from a browser click. Confirmed live:
    cloud.example.com only routes the WebDAV API (no web UI,
    no login page, / and /apps/files/ both 404), and an unauthenticated
    top-level browser navigation to the DAV endpoint trips Nextcloud's own
    CSRF "strict cookie" middleware and gets rejected outright -- there's
    no cookie to have, since there's no login page on this vhost to set
    one. curl doesn't trigger this (no Accept:text/html / Sec-Fetch-Mode
    navigate headers), which is why a plain server-side test looked fine.
    Routing through our own backend sidesteps it entirely: credentials
    stay server-side (never touch the client), same pattern as every
    other webdav_client caller in this codebase.

    2026-08-12 (rev 2): originally text/plain, which browsers render with
    their default white-background text viewer regardless of OS/app dark
    mode -- a jarring flashbang on a phone at night. Wrapping in a minimal
    HTML shell with prefers-color-scheme CSS fixes that. Content still
    goes through html.escape() into a <pre> block rather than being
    trusted as markup -- same XSS-safety property the plain-text response
    had (vault notes are operator-authored but not treated as safe HTML),
    just with theme-aware styling around it.
    """
    if not _vault_path_is_safe(path):
        raise HTTPException(400, "invalid path")
    from second_brain import webdav_client
    content = webdav_client.get(path)
    if content is None:
        raise HTTPException(404, "not found in vault")
    text = content.decode("utf-8", "replace")
    page = _VAULT_FILE_PAGE.format(
        title=html.escape(path.rsplit("/", 1)[-1]),
        path=html.escape(path),
        body=html.escape(text),
    )
    return HTMLResponse(page)


@app.get("/api/v1/vault/research")
async def vault_research_read(path: str = Query(...)) -> JSONResponse:
    """Tier-0 (unauthenticated) read access to second-brain RESEARCH content
    only -- for agent tools that cannot send an Authorization header or
    embed credentials in a URL (e.g. Cowork's fetch tool; confirmed live
    2026-08-16 that board-write auth hits the identical wall). Contrast with
    /api/v1/vault/file (Tier-1, any vault path, HTML) -- this is
    deliberately narrower in scope but requires no credential at all.

    Scoped to 01-Sources/personal-notes/<topic>/... where <topic> matches
    the vault convention: 'Research - $ANYTHING' (staging) or '$Anything
    Series' (final/in-flight drafts) -- see _vault_research_path_allowed.
    NOT pinned to any single named topic (e.g. Uber) -- any topic folder
    matching either pattern is in scope; everything else in the vault,
    including sibling personal-notes/ folders like 'Family Office - CTDI',
    is not.

    Same CUI/PII scrub gate as every other ingestion/serving path -- BLOCKS
    (never redacts) if the content looks like CUI radio data or contains an
    SSN-shaped token; a blocked file returns 422, nothing is served.

    200 -> {path, content}; 400 -> invalid/out-of-scope path; 404 -> not
    found; 422 -> blocked by CUI/PII scrub gate; 429 -> rate limited."""
    if not _vault_path_is_safe(path):
        raise HTTPException(status_code=400, detail="invalid path")
    if not _vault_research_path_allowed(path):
        raise HTTPException(
            status_code=400,
            detail=(
                f"path is outside the research-vault scope -- must be under "
                f"{_VAULT_RESEARCH_ROOT}/ (any topic folder there is in "
                f"scope), or one of: {', '.join(_VAULT_RESEARCH_EXTRA_PREFIXES)}"
            ),
        )
    now = time.monotonic()
    _vault_research_hits[:] = [t for t in _vault_research_hits if now - t < 60]
    if len(_vault_research_hits) >= 30:
        raise HTTPException(status_code=429, detail="vault research-read rate limit (30/min) exceeded")
    _vault_research_hits.append(now)

    from second_brain import webdav_client
    # webdav_client.get/put/list_files expect ACCOUNT-root-relative paths,
    # not BUSINESS_ROOT-relative -- every other caller in this codebase
    # prefixes webdav_client.BUSINESS_ROOT for exactly this reason (see
    # knowledge_graph/retrofit_links.py's comment on the same requirement).
    # `path` here and in the public API is the clean, business-relative form
    # (matches what the vault convention docs/humans actually use).
    content = webdav_client.get(f"{webdav_client.BUSINESS_ROOT}/{path}")
    if content is None:
        raise HTTPException(status_code=404, detail="not found in vault")
    text = content.decode("utf-8", "replace")
    try:
        _scrub_gate(text, source="vault-research-read")
    except _ScrubGateBlocked as e:
        raise HTTPException(status_code=422, detail=f"blocked by CUI/PII scrub gate: {e}")
    return JSONResponse({"path": path, "content": text})


@app.get("/api/v1/vault/research/list")
async def vault_research_list(path: str = Query(default=_VAULT_RESEARCH_ROOT)) -> JSONResponse:
    """List files (depth-1, non-recursive) under a research-vault folder --
    the discovery counterpart to /api/v1/vault/research (an agent needs to
    know what's there before it can read a specific file by exact path).
    Same Tier-0/scope rules as the read endpoint; path defaults to the
    research root itself, which lists the current topic folders.

    200 -> {path, files: [{path}, ...]}; 400 -> invalid/out-of-scope path;
    429 -> rate limited."""
    if not _vault_path_is_safe(path):
        raise HTTPException(status_code=400, detail="invalid path")
    # A bare extra-prefix folder itself (e.g. "04-Syntheses", no trailing
    # slash) must be listable too, not just files/subpaths under it --
    # _vault_research_path_allowed requires the trailing "/" form.
    is_bare_extra_root = any(
        path == p.rstrip("/") for p in _VAULT_RESEARCH_EXTRA_PREFIXES
    )
    if (
        path != _VAULT_RESEARCH_ROOT
        and not is_bare_extra_root
        and not _vault_research_path_allowed(path)
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"path is outside the research-vault scope -- must be "
                f"{_VAULT_RESEARCH_ROOT} itself, any topic folder under it, "
                f"or one of: {', '.join(_VAULT_RESEARCH_EXTRA_PREFIXES)}"
            ),
        )
    now = time.monotonic()
    _vault_research_hits[:] = [t for t in _vault_research_hits if now - t < 60]
    if len(_vault_research_hits) >= 30:
        raise HTTPException(status_code=429, detail="vault research-read rate limit (30/min) exceeded")
    _vault_research_hits.append(now)

    from second_brain import webdav_client
    # See the matching comment in vault_research_read -- webdav_client calls
    # need the BUSINESS_ROOT prefix; the public API (path in, and each
    # returned file's path) stays in the clean business-relative form, so
    # strip the prefix back off list_files()'s account-relative results.
    root_prefix = f"{webdav_client.BUSINESS_ROOT}/"
    raw_files = webdav_client.list_files(f"{webdav_client.BUSINESS_ROOT}/{path}")
    files = [
        {**f, "path": f["path"][len(root_prefix):] if f["path"].startswith(root_prefix) else f["path"]}
        for f in raw_files
    ]
    return JSONResponse({"path": path, "files": files})


@app.get("/api/v1/osint/scopes")
async def osint_list_scopes(
    tier: Tier = Depends(require_tier(Tier.T1)),
) -> JSONResponse:
    """Return all OSINT scopes (enabled and disabled).

    2026-08-13: tier-gated after a live pentest confirmed this endpoint
    was reachable unauthenticated on the public vhost, leaking full scope
    config (including EP-related scope types/query terms/feed URLs) --
    same class of gap as the vault/knowledge-graph fix earlier tonight,
    missed because this route lives in a different part of the file.
    """
    scopes = db.osint_get_scopes(enabled_only=False)
    return JSONResponse({"scopes": scopes, "count": len(scopes)})


@app.post("/api/v1/osint/scopes", status_code=201)
async def osint_create_scope(
    body: OsintScopeRequest,
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    """Create a new OSINT monitoring scope.

    2026-08-13: admin-gated, not just tier-gated -- scope config controls
    what URLs osint_monitor.py fetches on a schedule. Unauthenticated
    write access here is a real SSRF vector (attacker-supplied feed_urls
    fetched by this box), not just a data-exposure one, so this gets the
    stricter tier than the read above.
    """
    allowed_types = {
        # Generic
        "keyword", "person", "org", "topic", "geo",
        # Executive-protection context — get DC-area geo boost + EP narrative framing
        "ep_threat", "ep_principal", "ep_venue", "executive_protection",
        # Marketing / brand-intelligence context — [operator LLC abbreviation]Svcs brand narrative framing
        "brand_monitor", "market_intel", "competitor", "marketing",
        # Named/dated/venue-bound event (conference, summit, forum) — 2026-08-12,
        # SCHEMA_V32. Gets event/audience/genre metadata + its own narrative
        # framing, see osint_monitor.py's _EVENT_SCOPE_TYPES.
        "event",
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
        event_name=body.event_name.strip(),
        audience=body.audience.strip(),
        genre=body.genre.strip(),
    )
    return JSONResponse({"id": scope_id, "status": "created"})


@app.patch("/api/v1/osint/scopes/{scope_id}")
async def osint_update_scope(
    scope_id: int,
    body: dict,
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    """Partially update a scope (label, query_terms, feed_urls, push_threshold,
    enabled). Admin-gated -- see osint_create_scope."""
    if not db.osint_get_scope(scope_id):
        raise HTTPException(404, f"Scope {scope_id} not found")
    db.osint_update_scope(scope_id, **body)
    return JSONResponse({"id": scope_id, "status": "updated"})


@app.delete("/api/v1/osint/scopes/{scope_id}")
async def osint_delete_scope(
    scope_id: int,
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    """Delete an OSINT scope and all its items. Admin-gated -- see
    osint_create_scope."""
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


# ── Global ADS-B proxy — Tier 0 ──────────────────────────────────────────────

# Simple in-process cache — refresh every 30 seconds max.
# ADSB cache: keyed by rounded (lat, lon, radius) so map-pan queries are cached independently
_ADSB_CACHE: dict = {}   # key -> (result_dict, timestamp)
_ADSB_TTL: int = 30      # seconds

@app.get("/api/v1/adsb")
def get_adsb_live(
    lat: float = 38.8521,
    lon: float = -77.0377,
    radius: int = 250,
) -> JSONResponse:
    """Proxy ADS-B snapshot from airplanes.live around a configurable map center.

    Query params:
      lat    — center latitude  (default 38.8521 / KDCA)
      lon    — center longitude (default -77.0377 / KDCA)
      radius — search radius in NM, 1-250 (max enforced; API hard-limits at 250)

    Cache key is rounded to ~0.1-degree precision (~6 NM) to share results across
    minor pan events.  TTL is 30 seconds, matching the PWA poll interval.

    Response shape:
      {source, count, cached_at, center:{lat,lon,radius_nm}, aircraft:[...]}
    """
    global _ADSB_CACHE
    radius = max(1, min(int(radius), 250))
    # Round to ~6 NM grid for cache sharing
    key = f"{round(lat, 1)},{round(lon, 1)},{radius}"
    now = time.time()
    if key in _ADSB_CACHE:
        cached_result, cached_ts = _ADSB_CACHE[key]
        if (now - cached_ts) < _ADSB_TTL:
            return JSONResponse(cached_result)

    try:
        import requests as _req
        r = _req.get(
            f"https://api.airplanes.live/v2/point/{lat:.4f}/{lon:.4f}/{radius}",
            timeout=10,
            headers={"Accept": "application/json", "User-Agent": "corporatetraveldc-dispatch/1.0"},
        )
        r.raise_for_status()
        raw = r.json()
    except Exception as exc:
        if key in _ADSB_CACHE:
            stale = dict(_ADSB_CACHE[key][0])
            stale["stale"] = True
            return JSONResponse(stale)
        return JSONResponse(
            {"source": "airplanes.live", "count": 0, "aircraft": [],
             "center": {"lat": lat, "lon": lon, "radius_nm": radius},
             "error": str(exc)},
            status_code=503,
        )

    aircraft = []
    for ac in raw.get("ac", []):
        ac_lat = ac.get("lat")
        ac_lon = ac.get("lon")
        if ac_lat is None or ac_lon is None:
            continue
        aircraft.append({
            "hex":      ac.get("hex", ""),
            "flight":   (ac.get("flight") or "").strip(),
            "lat":      ac_lat,
            "lon":      ac_lon,
            "alt_baro": ac.get("alt_baro"),
            "gs":       ac.get("gs"),
            "track":    ac.get("track"),
            "squawk":   ac.get("squawk"),
            "type":     ac.get("t", ""),
            "r":        ac.get("r", ""),
            "desc":     ac.get("desc", ""),
            "category": ac.get("category", ""),
        })

    result = {
        "source":    "airplanes.live",
        "count":     len(aircraft),
        "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "center":    {"lat": lat, "lon": lon, "radius_nm": radius},
        "aircraft":  aircraft,
    }
    _ADSB_CACHE[key] = (result, now)
    # Prune: keep at most 30 cache keys
    if len(_ADSB_CACHE) > 30:
        oldest_keys = sorted(_ADSB_CACHE, key=lambda k: _ADSB_CACHE[k][1])
        for k in oldest_keys[:10]:
            del _ADSB_CACHE[k]
    return JSONResponse(result)


# ── FAA Aircraft Registry — Tier 0 ────────────────────────────────────────────

@app.get("/api/v1/aircraft/{identifier}")
async def get_aircraft(identifier: str) -> JSONResponse:
    """Look up an aircraft by N-number/tail or ICAO hex, cross-referencing
    the local FAA registry cache AND OpenSky's registry (added 2026-07-23,
    operator directive: flight numbers resolve to a tail number, then that
    tail is cross-referenced against BOTH registries to derive an
    authoritative hex — don't trust a single source's self-reported hex
    alone). FAA is US-N-number-only and checked first when the identifier
    looks like one; OpenSky covers foreign registrations FAA never will and
    is checked either as the primary source (non-N-number tails) or as a
    cross-check against FAA's hex (N-number tails present in both).

    - N-number:  N12345, 12345 (leading N optional)
    - ICAO hex:  a1b2c3  (6 hex chars, case-insensitive)
    - Foreign tail: G-EUYA, D-AIBL, etc. — FAA will never have these,
      OpenSky is the only source.

    Returns a `source` field ("faa", "opensky", or "faa+opensky") and, when
    both registries have the tail, a `hex_mismatch` flag — true means FAA's
    and OpenSky's recorded hex for this tail disagree, which is itself a
    signal worth surfacing (stale registry data on one side, or a
    re-registration in progress) rather than silently picking one.
    Returns 404 if not found in either registry, or if neither registry has
    been imported yet.
    """
    try:
        db.init_db_v11()
        db.init_db_v27()
    except Exception:
        pass

    ident = identifier.strip()
    faa_record: dict | None = None
    osky_record: dict | None = None

    import re as _re
    is_hex = bool(_re.fullmatch(r"[0-9a-fA-F]{6}", ident))

    if is_hex:
        faa_record = db.faa_lookup_by_hex(ident)
        osky_record = db.opensky_lookup_by_hex(ident)
    else:
        faa_record = db.faa_lookup_by_n_number(ident)
        osky_record = db.opensky_lookup_by_registration(ident)

    if not faa_record and not osky_record:
        faa_counts = db.faa_registry_count()
        opensky_total = db.opensky_registry_count()
        if faa_counts["total"] == 0 and opensky_total == 0:
            return JSONResponse(
                {"error": "Neither FAA nor OpenSky registry has been imported yet"},
                status_code=503,
            )
        return JSONResponse(
            {"error": f"Aircraft '{ident}' not found in FAA or OpenSky registry"},
            status_code=404,
        )

    faa_hex = (faa_record.get("mode_s_hex") or "").lower() if faa_record else None
    osky_hex = (osky_record.get("icao24") or "").lower() if osky_record else None

    # FAA's own manufacturer/model decode via ACFTREF.txt (added 2026-08-02),
    # independent of OpenSky's model/typecode fields below -- deliberate
    # redundancy so the two sources can be cross-checked against each other.
    acftref_record = (
        db.faa_acftref_lookup(faa_record.get("mfr_mdl_code"))
        if faa_record else None
    )

    if faa_record and osky_record:
        source = "faa+opensky"
        hex_mismatch = bool(faa_hex and osky_hex and faa_hex != osky_hex)
        # FAA is authoritative for US N-numbers when both agree or FAA alone
        # has a value; OpenSky fills in only if FAA's hex is somehow blank.
        authoritative_hex = faa_hex or osky_hex
    elif faa_record:
        source = "faa"
        hex_mismatch = False
        authoritative_hex = faa_hex
    else:
        source = "opensky"
        hex_mismatch = False
        authoritative_hex = osky_hex

    return JSONResponse({
        "identifier":      ident,
        "source":          source,
        "hex_id":          authoritative_hex,
        "hex_mismatch":    hex_mismatch,
        "faa": ({
            "n_number":        faa_record.get("n_number"),
            "mode_s_hex":      faa_record.get("mode_s_hex"),
            "registrant_name": faa_record.get("registrant_name"),
            "city":            faa_record.get("city"),
            "state":           faa_record.get("state"),
            "year_mfr":        faa_record.get("year_mfr"),
            "mfr_mdl_code":    faa_record.get("mfr_mdl_code"),
            "serial_number":   faa_record.get("serial_number"),
            "status_code":     faa_record.get("status_code"),
            "type_aircraft":   faa_record.get("type_aircraft"),
            "type_engine":     faa_record.get("type_engine"),
            "expiration_date": faa_record.get("expiration_date"),
            "last_action_date":faa_record.get("last_action_date"),
            "ladd":            faa_record.get("ladd", False),
            "manufacturer":    acftref_record.get("manufacturer") if acftref_record else None,
            "model":           acftref_record.get("model") if acftref_record else None,
        } if faa_record else None),
        "opensky": ({
            "icao24":            osky_record.get("icao24"),
            "registration":      osky_record.get("registration"),
            "manufacturer_name": osky_record.get("manufacturer_name"),
            "model":             osky_record.get("model"),
            "typecode":          osky_record.get("typecode"),
            "operator":          osky_record.get("operator"),
            "owner":             osky_record.get("owner"),
            "registered":        osky_record.get("registered"),
            "built":             osky_record.get("built"),
        } if osky_record else None),
        # Back-compat top-level fields for existing callers that read the
        # pre-2026-07-23 flat FAA-only shape directly.
        "n_number":        faa_record.get("n_number") if faa_record else None,
        "mode_s_hex":      authoritative_hex,
        "registrant_name": faa_record.get("registrant_name") if faa_record else osky_record.get("owner"),
        "ladd":            faa_record.get("ladd", False) if faa_record else False,
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


# ---------------------------------------------------------------------------
# Bandwidth priority override (SWIM vs. NEXRAD) — operator toggle
# ---------------------------------------------------------------------------
# 2026-07-21. the operator wants a bidirectional manual override for when bandwidth
# is tight: declare 'swim' (SWIM/NMS ingest stays full-rate) or 'nexrad' (a
# future NEXRAD Level II puller gets priority, SWIM's fdps feed backs off)
# or 'auto' (no override). See SCHEMA_V20 in common/db.py for the full
# rationale, including the honest caveat that the 'swim' direction has
# nothing on the other side to defer yet since no Level II puller exists.

class BandwidthPriorityRequest(BaseModel):
    priority: str            # 'auto' | 'swim' | 'nexrad' | 'weather'
    reason: str = ""
    ttl_seconds: float | None = None


@app.post("/admin/bandwidth-priority")
async def set_bandwidth_priority_route(
    body: BandwidthPriorityRequest,
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    if body.priority not in ("auto", "swim", "nexrad", "weather"):
        raise HTTPException(
            status_code=400,
            detail=f"priority must be 'auto', 'swim', 'nexrad', or 'weather' — got {body.priority!r}",
        )
    state = db.set_bandwidth_priority(
        priority=body.priority, set_by="admin", reason=body.reason,
        ttl_seconds=body.ttl_seconds,
    )
    db.insert_trigger(str(uuid.uuid4()), "bandwidth_priority_set",
                       {"priority": body.priority, "reason": body.reason})
    return JSONResponse(state)


@app.delete("/admin/bandwidth-priority")
async def clear_bandwidth_priority_route(
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    state = db.set_bandwidth_priority(priority="auto", set_by="admin", reason="cleared")
    return JSONResponse(state)


# ---------------------------------------------------------------------------
# Sudo approval-gate (added 2026-07-27, see SUDO_JUSTIFICATION_PROPOSAL.md)
#
# Claude creates a pending request (admin-token gated, called from the Pi
# side), pushes an ntfy alert with Allow/Deny action buttons, then polls
# status. The resolve endpoint the phone actually taps is deliberately
# Tier 0 (no bearer auth) -- Cloudflare strips Authorization headers on the
# way through the tunnel, so a token-gated endpoint would never work from a
# phone that isn't on the tailnet. Security here comes from the id itself:
# a UUID4 is 122 bits of entropy, functionally a magic link, and the DB only
# accepts one resolution per id (already-resolved or expired requests can't
# be re-resolved -- see resolve_approval_request()'s WHERE clause).
# ---------------------------------------------------------------------------

class ApprovalRequestCreate(BaseModel):
    command_pattern: str
    command: str
    reasoning: str = ""
    ttl_seconds: float = 600.0


@app.post("/admin/approval-requests", status_code=201)
async def create_approval_request_route(
    body: ApprovalRequestCreate,
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    request_id = str(uuid.uuid4())
    result = db.create_approval_request(
        request_id, body.command_pattern, body.command,
        reasoning=body.reasoning, ttl_seconds=body.ttl_seconds,
    )
    return JSONResponse(result, status_code=201)


@app.get("/admin/approval-requests/{request_id}")
async def get_approval_request_route(
    request_id: str,
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    row = db.get_approval_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="approval request not found")
    return JSONResponse(row)


@app.get("/admin/approval-requests/{request_id}/resolve")
async def resolve_approval_request_route(
    request_id: str,
    action: str = Query(..., pattern="^(allow|deny)$"),
) -> JSONResponse:
    """Tier 0 -- deliberately no auth dependency. See module comment above."""
    row = db.resolve_approval_request(request_id, action)
    if row is None:
        raise HTTPException(status_code=404, detail="approval request not found")
    return JSONResponse(row)


@app.get("/admin/approval-requests")
async def list_approval_requests_route(
    command_pattern: Optional[str] = Query(default=None),
    since_days: float = Query(default=7.0),
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    """Recent approvals for a pattern -- backs the frequency-promotion check.
    If command_pattern is omitted, just reports the count for an empty
    pattern (0) rather than erroring, since this is a convenience read, not
    a mutation."""
    since = time.time() - since_days * 86400
    count = db.count_recent_approvals(command_pattern or "", since)
    return JSONResponse({
        "command_pattern": command_pattern,
        "since_days": since_days,
        "allowed_count": count,
        "promotion_candidate": count > 2,
    })


# ---------------------------------------------------------------------------
# Watchdog status endpoint
# ---------------------------------------------------------------------------

@app.get("/admin/watchdog/status")
async def watchdog_status(
    tier: Tier = Depends(require_admin),
) -> JSONResponse:
    """Return last ctdi-watchdog run result.

    Reads /var/lib/corporatetraveldc/watchdog-last-run.json written by
    /opt/corporatetraveldc/bin/ctdi-watchdog.sh after each 5-minute run.
    """
    status_path = pathlib.Path("/var/lib/corporatetraveldc/watchdog-last-run.json")
    if not status_path.exists():
        return JSONResponse({"available": False, "reason": "no run recorded yet"})
    try:
        data = json.loads(status_path.read_text())
        data["age_seconds"] = int(time.time()) - data.get("unix", 0)
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"available": False, "error": str(exc)})
