"""
demo.demo_api — read-only playback API over the sovereign demo archive.

Serves the same GET /api/v1/{endpoint} path shape as the live web app
(src/web/main.py), so an unmodified `runner` frontend/backend can point
its DISPATCH_BASE_URL at this service instead of the live one and get a
drop-in "demo mode" -- no runner code changes required.

Privacy boundary (2026-08-14, physically enforced, not just cosmetic):
this service reads ONLY /var/lib/corporatetraveldc-demo-source/demo-source.db
-- a sovereign file, separate top-level directory from the live
/var/lib/corporatetraveldc tree, populated exclusively by
scripts/scrub-demo-source.py (the one component trusted to touch live
data, running host-side on its own schedule, never inside a container).
This process holds no live-DB connection at all -- not app-level
"we choose not to query it," but literally no code path that can. See
docs/DEMO_DATA_ISOLATION_PLAN_2026-08-13.md for the full design and
docs/PENTEST_CLEARANCE_CHECK_2026-08-13.md for the F6 finding this closes.

The invariant is stronger than the pre-2026-08-14 version: it used to be
"only OLD data is reachable" (temporal only, but reading the live DB
directly). It is now "only data that has passed scrub+verify and been
PROMOTED is reachable, and it is always at least one promotion cycle
behind now" (artifact-based AND temporal) -- see _virtual_timestamp() for
the one function that enforces the temporal half.

Loop design:
  - ANCHOR = window_end - window_days, where window_end comes from the
    sovereign file's own meta table, written only by scrub-demo-source.py
    on a promotion. The anchor advances only when a promotion writes a
    new window_end -- NEVER as a function of wall-clock time at query
    time. (Pre-2026-08-14 this was MIN(captured_at) -- the oldest-ever
    snapshot, permanently. That temporal-only property still holds; this
    is additionally gated on the artifact having been promoted.)
  - LOOP_DAYS = 14, matching demo.recorder.SEED_TARGET (the point at
    which the archive is considered to have "enough" history for a
    believable demo cycle).
  - virtual_timestamp = ANCHOR + ((now - ANCHOR) mod LOOP_DAYS)
  - Each endpoint returns its most recent real (scrubbed, promoted)
    snapshot at or before virtual_timestamp -- i.e. "what the platform
    actually showed" at that point in the promoted history, replayed on
    an endless loop.

If the archive doesn't yet span LOOP_DAYS (seed not ready), endpoints
return 503 rather than silently serving whatever's on hand -- a partial
loop would repeat obviously and undercut the demo rather than protect
anything, but failing loudly here is simpler to reason about than
partial-window logic, and the readiness endpoint already tells the
caller exactly how many days remain.
"""
import os
import sqlite3
import zlib
import httpx
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Query, Header, HTTPException, status, Depends
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

from demo.recorder import RETENTION_TIERS
from demo import profiles as demo_profiles

DEMO_DB   = os.environ.get("DEMO_DB", "/var/lib/corporatetraveldc-demo-source/demo-source.db")
LOOP_DAYS = int(os.environ.get("DEMO_LOOP_DAYS", "14"))

# Optional fixed anchor override (ISO date/datetime), added 2026-08-02.
# Without this, _anchor() below picks the EARLIEST point where every
# endpoint has data -- which, as the archive grows, permanently loops the
# OLDEST slice of history forever (the archive's first 14 days), since the
# anchor never moves once cached. the operator found this produced a thinner-
# feeling loop than the archive now supports (07-09 through 07-23 was the
# coldest-start slice; 07-19 through 08-01 has full daily coverage on
# every core endpoint). This override lets the loop's START be moved to a
# specific, deliberately-chosen historical date -- still a FIXED point,
# still never a function of true wall-clock "now", so the hard privacy
# boundary this module's docstring describes (today's real data can never
# reach this path) is unchanged. It's a curation knob, not a live window.
DEMO_ANCHOR_OVERRIDE = os.environ.get("DEMO_ANCHOR_OVERRIDE", "").strip()

# Admin gate for profile management (create/list/revoke passwords). A single
# shared secret, not the main app's multi-tier token DB -- see profiles.py's
# module docstring for why this stays decoupled from common.db. Reuses the
# same admin token value operationally, but demo_api.py never queries the
# live token store to check it.
DEMO_ADMIN_TOKEN = os.environ.get("DEMO_ADMIN_TOKEN")


def _require_admin(authorization: str | None = Header(default=None)) -> None:
    if not DEMO_ADMIN_TOKEN:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                             "DEMO_ADMIN_TOKEN not configured on this service")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or supplied != DEMO_ADMIN_TOKEN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin token required")


def _current_tier(span_days: int) -> tuple[str, str | None]:
    """Which retention tier the archive has reached, and the next one up.
    RETENTION_TIERS is ordered smallest-to-largest by construction; walk it
    to find the highest tier crossed and whatever comes after it."""
    items = list(RETENTION_TIERS.items())
    reached = None
    next_tier = None
    for i, (label, days) in enumerate(items):
        if span_days >= days:
            reached = label
        else:
            next_tier = label
            break
    return reached or "pre-2w", next_tier

# Same endpoint set demo.recorder captures -- keep in sync with
# demo.recorder.ENDPOINTS by design (both read from the same live API).
ENDPOINTS = [
    "tfr", "weather", "alerts", "cps", "notams",
    "amtrak", "opsplan", "route", "brief",
    "knowledge_graph_meta", "osint_feed", "board", "board_threads",
]

# Storage identifier -> demo-served URL path, for entries above whose live
# path has more than one segment. Keep in sync with
# demo.recorder.ENDPOINT_PATHS.
ENDPOINT_PATHS: dict[str, str] = {
    "knowledge_graph_meta": "knowledge-graph/meta",
    "osint_feed":           "osint/feed",
    "board_threads":        "board/threads",
}

# 2026-08-26 fix (Opus blind review C-12 class): this is the API backing
# the PUBLIC demo runner -- unlike web/main.py and runner/main.py, it
# never set docs_url/redoc_url/openapi_url at all, so it was serving
# Swagger UI, ReDoc, AND the raw schema at their FastAPI defaults on the
# most internet-exposed of the three apps.
app = FastAPI(title="corporatetraveldc-demo-api", docs_url=None, redoc_url=None, openapi_url=None)

_window_end_cache: datetime | None = None
_window_end_cache_checked = False
_legacy_anchor_cache: datetime | None = None


def _conn() -> sqlite3.Connection:
    # Read-only URI connection -- this process must never write
    # DEMO_DB (the sovereign demo-source file); scripts/scrub-demo-source.py
    # is the only writer, and mixing writers risks lock contention against
    # a service that also has a public-facing surface.
    return sqlite3.connect(f"file:{DEMO_DB}?mode=ro", uri=True)


def _window_end(conn: sqlite3.Connection) -> datetime | None:
    """The sovereign file's own meta.window_end -- written exclusively by
    scripts/scrub-demo-source.py when it promotes newly scrubbed content.
    Deliberately window_days-independent (unlike the anchor derived from
    it), so it's safe to cache for process lifetime the same way the old
    single anchor value was: it only changes when a NEW promotion runs,
    which means restarting this service (a fresh worker/deploy), not
    within a single process's lifetime."""
    global _window_end_cache, _window_end_cache_checked
    if _window_end_cache_checked:
        return _window_end_cache
    _window_end_cache_checked = True
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='window_end'").fetchone()
    except sqlite3.OperationalError:
        row = None
    if row and row[0]:
        _window_end_cache = datetime.fromisoformat(row[0])
    return _window_end_cache


def _anchor(conn: sqlite3.Connection, window_days: int = LOOP_DAYS) -> datetime | None:
    """2026-08-14: trailing anchor derived from _window_end(), NOT the
    oldest-ever snapshot. anchor = window_end - window_days, so the loop
    replays the most RECENTLY promoted window_days-sized slice, advancing
    only on the next promotion (typically nightly -- see the refresh
    timer), never at query time. This is what makes "seeded from current
    state, not a fixed old date" (the original usability complaint this
    whole redesign responds to) hold without weakening the temporal
    privacy boundary -- the anchor still only moves in discrete,
    promotion-gated steps. NOT itself cached (unlike _window_end) because
    it depends on the caller's window_days, which varies by access
    profile (RETENTION_TIERS) -- the arithmetic is cheap, only the DB read
    behind _window_end() needs caching.

    Falls back to the pre-2026-08-14 MIN()-based derivation only if
    meta.window_end is absent (a sovereign file that predates any
    promotion, or DEMO_ANCHOR_OVERRIDE is set) -- see that fallback's own
    comment for why LATEST-of-earliest, not naive MIN(), matters there.
    """
    global _legacy_anchor_cache
    if DEMO_ANCHOR_OVERRIDE:
        return datetime.fromisoformat(DEMO_ANCHOR_OVERRIDE)

    window_end = _window_end(conn)
    if window_end is not None:
        return window_end - timedelta(days=window_days)

    # Fallback: no promotion has ever run (or meta table doesn't exist yet
    # on an old-shape file) -- same LATEST-of-each-endpoint's-earliest
    # logic the pre-2026-08-14 version used, so a not-yet-promoted file
    # still degrades sanely instead of erroring. This one IS cacheable as
    # a single value -- it doesn't depend on window_days.
    if _legacy_anchor_cache is not None:
        return _legacy_anchor_cache
    try:
        row = conn.execute(
            "SELECT MAX(first_seen) FROM ("
            "  SELECT endpoint, MIN(captured_at) AS first_seen FROM snapshots GROUP BY endpoint"
            ")"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or not row[0]:
        return None
    _legacy_anchor_cache = datetime.fromisoformat(row[0])
    return _legacy_anchor_cache


def _virtual_timestamp(conn: sqlite3.Connection, window_days: int = LOOP_DAYS,
                       speed: float = 1.0) -> datetime | None:
    """The one function that decides what point in history to replay.

    Never touches real wall-clock "now" beyond computing elapsed time
    since a fixed historical anchor -- the result is always somewhere
    inside [ANCHOR, ANCHOR + window_days), never today's real date, unless
    the archive itself is younger than window_days (handled by the caller
    via the 503 seed-not-ready path).

    window_days/speed, added 2026-07-31 for the password-gated access
    profiles: window_days sets how much archived history one loop covers
    (still bounded by RETENTION_TIERS on the caller side -- this function
    doesn't validate that, callers must), speed scales how fast virtual
    time advances relative to wall-clock time (>1 = condensed/faster loop
    for a pitch-deck-length demo, 1 = real-time pace for a full
    walkthrough). Both default to the original fixed behavior (LOOP_DAYS,
    1.0) so a request with no session/window/speed params is unchanged
    from before this existed.
    """
    anchor = _anchor(conn, window_days)
    if anchor is None:
        return None
    now = datetime.now(timezone.utc)
    elapsed = (now - anchor) * speed
    loop_span = timedelta(days=window_days)
    offset = timedelta(seconds=elapsed.total_seconds() % loop_span.total_seconds())
    return anchor + offset


def _seed_ready(conn: sqlite3.Connection, window_days: int = LOOP_DAYS) -> tuple[bool, int]:
    anchor = _anchor(conn, window_days)
    if anchor is None:
        return False, 0
    newest_row = conn.execute("SELECT MAX(captured_at) FROM snapshots").fetchone()
    newest = datetime.fromisoformat(newest_row[0]) if newest_row and newest_row[0] else anchor
    span_days = (newest - anchor).total_seconds() / 86400
    return span_days >= window_days, int(span_days)


def _resolve_playback_params(session: str | None, window: str | None,
                              speed_param: float | None) -> tuple[int, float, str | None]:
    """Figures out (window_days, speed, profile_label) for one request.
    Priority: valid session token > explicit window/speed query params >
    the original fixed default. A window label ('2w'/'8w'/etc, matching
    demo.recorder.RETENTION_TIERS) or a raw integer day count are both
    accepted for the query-param path, since the operator's own Tailnet browsing
    is meant to bypass the password system entirely, not route through it."""
    if session:
        payload = demo_profiles.verify_session_token(session)
        if payload:
            return payload["window_days"], payload["speed"], payload.get("label")
        # Invalid/expired session: fall through to the open default rather
        # than error -- an expired demo link should degrade to the plain
        # 14-day loop, not break.

    if window:
        window_days = RETENTION_TIERS.get(window)
        if window_days is None:
            try:
                window_days = int(window)
            except ValueError:
                window_days = LOOP_DAYS
    else:
        window_days = LOOP_DAYS

    speed = speed_param if speed_param is not None else 1.0
    return window_days, speed, None


@app.get("/healthz")
async def healthz() -> JSONResponse:
    exists = os.path.exists(DEMO_DB)
    return JSONResponse({"ok": exists, "demo_db": DEMO_DB, "loop_days": LOOP_DAYS})


@app.get("/api/v1/demo/readiness")
async def demo_readiness() -> JSONResponse:
    """Mirrors the live app's readiness endpoint, queried from this service
    directly -- lets this container be health-checked independently of
    the main web app's uptime."""
    conn = _conn()
    try:
        ready, span_days = _seed_ready(conn)
        total = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        tier, next_tier = _current_tier(span_days)
        return JSONResponse({
            "ready": ready,
            "loop_days": LOOP_DAYS,
            "archive_span_days": span_days,
            "total_snapshots": total,
            "mode": "demo-playback",
            "retention_tier": tier,
            "next_retention_tier": next_tier,
        })
    finally:
        conn.close()


def _brief_archive_lookup(brief_type: str, at: datetime) -> dict | None:
    """Most recent brief_archive row of `brief_type` at or before virtual
    timestamp `at`. 2026-08-14: reads from the SAME sovereign connection
    (_conn(), DEMO_DB) as every other endpoint -- brief_archive now lives
    in the sovereign file, populated by scripts/scrub-demo-source.py's
    scrub+promote pass, same as `snapshots`. This function used to open a
    dedicated live-DB connection (_live_conn(), since deleted) and query
    the real production brief_archive table directly -- that was F6, the
    thing this whole redesign closes. Returns None if empty for that
    type/window -- callers fall back to the same "not available yet" text
    the live app itself returns in that case."""
    c = _conn()
    c.row_factory = sqlite3.Row
    try:
        row = c.execute(
            "SELECT id, generated_at, brief_type, content, source FROM brief_archive "
            "WHERE brief_type=? AND generated_at<=? ORDER BY generated_at DESC LIMIT 1",
            (brief_type, at.isoformat()),
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        # brief_archive table doesn't exist yet on a not-yet-promoted
        # sovereign file -- same "nothing available" outcome as empty.
        return None
    finally:
        c.close()


def _brief_archive_history(brief_type: str | None, at: datetime, limit: int) -> list[dict]:
    """Most recent `limit` brief_archive rows at or before virtual
    timestamp `at`, optionally filtered by type -- backs /brief/history.
    Same sovereign-file-only read path as _brief_archive_lookup() above."""
    c = _conn()
    c.row_factory = sqlite3.Row
    try:
        if brief_type:
            rows = c.execute(
                "SELECT id, generated_at, brief_type, source FROM brief_archive "
                "WHERE brief_type=? AND generated_at<=? ORDER BY generated_at DESC LIMIT ?",
                (brief_type, at.isoformat(), limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, generated_at, brief_type, source FROM brief_archive "
                "WHERE generated_at<=? ORDER BY generated_at DESC LIMIT ?",
                (at.isoformat(), limit),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        c.close()


def _load_snapshot(conn: sqlite3.Connection, endpoint: str, at: datetime) -> tuple[str, str, bool] | None:
    """Most recent snapshot for `endpoint` at or before virtual timestamp `at`.
    Returns (payload_text, captured_at, synthetic) or None. `synthetic`
    reflects scripts/scrub-demo-source.py's replay-fill mechanism (2026-08-14)
    -- a real, already-scrubbed payload re-dated to cover a gap, never
    fabricated content. Real rows always win: this query has no idea which
    rows are synthetic, it just picks the newest row <= `at`, and a real
    row promoted later for that same day naturally out-dates any synthetic
    filler for that day."""
    try:
        row = conn.execute(
            "SELECT payload, payload_hash, compressed, captured_at, synthetic FROM snapshots "
            "WHERE endpoint=? AND captured_at<=? ORDER BY captured_at DESC LIMIT 1",
            (endpoint, at.isoformat()),
        ).fetchone()
    except sqlite3.OperationalError:
        # Sovereign file predates the synthetic column (pre-2026-08-14).
        row = conn.execute(
            "SELECT payload, payload_hash, compressed, captured_at FROM snapshots "
            "WHERE endpoint=? AND captured_at<=? ORDER BY captured_at DESC LIMIT 1",
            (endpoint, at.isoformat()),
        ).fetchone()
        if row is not None:
            row = (*row, 0)
    if row is None:
        return None
    payload, _payload_hash, compressed, captured_at, synthetic = row
    if compressed:
        text = zlib.decompress(payload).decode("utf-8", errors="replace")
    else:
        text = payload if isinstance(payload, str) else payload.decode("utf-8", errors="replace")
    return text, captured_at, bool(synthetic)


def _endpoint_route(endpoint: str):
    async def _handler(
        session: str | None = Query(default=None),
        window: str | None = Query(default=None),
        speed: float | None = Query(default=None),
    ) -> Response:
        conn = _conn()
        try:
            window_days, playback_speed, profile_label = _resolve_playback_params(
                session, window, speed
            )
            ready, span_days = _seed_ready(conn, window_days)
            if not ready:
                return JSONResponse(
                    {
                        "error": "demo_seed_not_ready",
                        "detail": f"archive has {span_days}/{window_days} days of history",
                    },
                    status_code=503,
                )
            vt = _virtual_timestamp(conn, window_days, playback_speed)
            found = _load_snapshot(conn, endpoint, vt)
            if found is None:
                return JSONResponse(
                    {"error": "no_snapshot", "endpoint": endpoint}, status_code=404
                )
            text, captured_at, synthetic = found
            headers = {
                "X-Demo-Mode": "true",
                "X-Demo-Replayed-From": captured_at,
                "X-Demo-Window-Days": str(window_days),
                "X-Demo-Speed": str(playback_speed),
            }
            if synthetic:
                # Real, already-scrubbed data re-dated to fill a coverage
                # gap (scripts/scrub-demo-source.py replay_fill_gaps(),
                # 2026-08-14) -- never fabricated content. Surfaced here for
                # transparency; not shown in any UI today.
                headers["X-Demo-Synthetic"] = "true"
            if profile_label:
                headers["X-Demo-Profile"] = profile_label
            # brief is PlainTextResponse on the live app (src/web/main.py
            # get_brief()) -- never JSON. Serving it as application/json
            # made the runner's proxy_dispatch() try r.json() on plain
            # prose text and 502 every time. Fixed 2026-08-02.
            media_type = "text/plain" if endpoint == "brief" else "application/json"
            return Response(content=text, media_type=media_type, headers=headers)
        finally:
            conn.close()

    return _handler


# Register one route per archived endpoint -- exact path parity with the
# live app (src/web/main.py's /api/v1/{name}) is the whole point: it lets
# an unmodified runner instance swap DISPATCH_BASE_URL and nothing else.
for _ep in ENDPOINTS:
    app.add_api_route(
        f"/api/v1/{ENDPOINT_PATHS.get(_ep, _ep)}", _endpoint_route(_ep), methods=["GET"]
    )


# ── Brief sub-routes: weekly / history / by-type-or-id ──────────────────────
# Added 2026-08-02. The bare /api/v1/brief route above (registered via the
# ENDPOINTS loop, "ops" type) only ever covered the ops brief. weekly and
# ep-advance were never wired at all -- BriefView.jsx's fetches for those
# 404'd against this service with no replay data behind them. Originally
# fixed (2026-08-02) by reading brief_archive directly from the live app's
# DB -- a deliberate shortcut, "rather than trying to grow a second
# recorder archive from scratch," documented as never paid down. That was
# F6 (the redteam pentest finding, 2026-08-13): logical-not-physical demo
# isolation. 2026-08-14: paid down. _brief_archive_lookup()/
# _brief_archive_history() now read brief_archive from the SOVEREIGN file
# (populated by scripts/scrub-demo-source.py's scrub+promote pass), the
# same connection every other route here uses. No live-DB connection
# exists anywhere in this process anymore.

@app.get("/api/v1/brief/weekly")
async def demo_brief_weekly(
    session: str | None = Query(default=None),
    window: str | None = Query(default=None),
    speed: float | None = Query(default=None),
) -> Response:
    conn = _conn()
    try:
        window_days, playback_speed, _profile_label = _resolve_playback_params(
            session, window, speed
        )
        vt = _virtual_timestamp(conn, window_days, playback_speed)
        if vt is None:
            return PlainTextResponse("No weekly summary available yet.")
        row = _brief_archive_lookup("weekly", vt)
        text = row["content"] if row else "No weekly summary available yet."
        headers = {"X-Demo-Mode": "true"}
        if row:
            headers["X-Demo-Replayed-From"] = row["generated_at"]
        return PlainTextResponse(text, headers=headers)
    finally:
        conn.close()


@app.get("/api/v1/brief/history")
async def demo_brief_history(
    limit: int = 7,
    type: str | None = None,
    session: str | None = Query(default=None),
    window: str | None = Query(default=None),
    speed: float | None = Query(default=None),
) -> JSONResponse:
    conn = _conn()
    try:
        window_days, playback_speed, _profile_label = _resolve_playback_params(
            session, window, speed
        )
        vt = _virtual_timestamp(conn, window_days, playback_speed)
        if vt is None:
            return JSONResponse([])
        entries = _brief_archive_history(type, vt, min(max(limit, 1), 30))
        return JSONResponse(entries)
    finally:
        conn.close()


@app.get("/api/v1/brief/{brief_ref}")
async def demo_brief_by_ref(
    brief_ref: str,
    session: str | None = Query(default=None),
    window: str | None = Query(default=None),
    speed: float | None = Query(default=None),
) -> Response:
    """Type slug (e.g. "ep-advance") -> most recent brief of that type at
    or before the virtual timestamp. Integer IDs (the live app's other
    supported form) aren't meaningfully replayable through a virtual
    timestamp, so those just 404 here rather than leaking a real
    non-time-scoped row -- BriefView.jsx only ever calls this with a type
    slug in practice."""
    conn = _conn()
    try:
        window_days, playback_speed, _profile_label = _resolve_playback_params(
            session, window, speed
        )
        vt = _virtual_timestamp(conn, window_days, playback_speed)
        if vt is None:
            return PlainTextResponse(f"No {brief_ref} brief available yet.", status_code=404)
        row = _brief_archive_lookup(brief_ref, vt)
        if not row:
            return PlainTextResponse(f"No {brief_ref} brief available yet.", status_code=404)
        return PlainTextResponse(row["content"], headers={
            "X-Demo-Mode": "true",
            "X-Demo-Replayed-From": row["generated_at"],
        })
    finally:
        conn.close()


@app.get("/api/v1/wx-config")
async def wx_config_stub() -> JSONResponse:
    """Static NEXRAD/radar reference config, mirroring the live app's
    /api/v1/wx-config. Never wired into ENDPOINTS because it isn't
    time-series snapshot data -- added directly here 2026-08-02 after
    confirming demo mode's WX tab was 404ing on this path with no
    fallback, unrelated to DEMO_ANCHOR_OVERRIDE. `operator` stays null in
    demo mode on purpose -- there's no real operator-configured radar
    source to show a demo visitor; the public NWS radar_url is what
    renders, same loop gif a live visitor would see, fetched client-side
    directly from radar.weather.gov.
    """
    return JSONResponse({
        "nws": {
            "name": "NEXRAD",
            "wfo": "LWX",
            "radar_site": "KLWX",
            "radar_url": "https://radar.weather.gov/ridge/standard/KLWX_loop.gif",
            # Mirrors the live config's national composite loop, added
            # 2026-08-03 -- see main.py's nws block comment.
            "radar_url_conus": "https://radar.weather.gov/ridge/standard/CONUS_loop.gif",
            "station_page": "https://radar.weather.gov/station/klwx/standard",
        },
        "operator": None,
        # WPC prog series mirrored from the live config 2026-08-03. Real,
        # live NOAA imagery for demo visitors too -- deliberately NOT
        # replay-captured this cycle (matches how the NEXRAD radar_url
        # above already works in demo mode: real-time, not archived). Once
        # the next demo window/anchor cycle starts, this can move to a
        # captured-and-replayed model like weather/briefs if wanted.
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


@app.get("/api/v1/feeds")
async def feeds_stub() -> JSONResponse:
    """The runner's context builder also polls /api/v1/feeds for freshness
    display. The recorder doesn't archive this endpoint (it's a live-only
    health signal, not demo-relevant content), so this returns an honest
    static stub instead of a 404 the frontend would otherwise render as
    a broken widget."""
    return JSONResponse({"mode": "demo-playback", "note": "feed freshness not tracked in demo mode"})


# ---------------------------------------------------------------------------
# Password-gated access profiles, added 2026-07-31.
#
# Public: POST /api/v1/demo/login exchanges a plaintext password for a
# signed session token (see demo.profiles.issue_session_token) that the
# frontend then attaches as ?session=... on the per-endpoint GETs above.
#
# Admin: create/list/revoke profiles, gated by DEMO_ADMIN_TOKEN (a single
# shared secret env var, not the live app's token DB -- see profiles.py).
# These are meant to be called from Tailscale/trusted context, same as the
# rest of the platform's admin surface, not from the public demo hostname.
# ---------------------------------------------------------------------------

class DemoLoginRequest(BaseModel):
    password: str


class DemoProfileCreateRequest(BaseModel):
    label: str
    window: str          # one of RETENTION_TIERS' keys ("2w", "8w", ...) --
                          # for auto_scale=True this is just the initial
                          # floor, the live tier takes over from the first
                          # login onward.
    speed: float = 1.0
    password: str | None = None   # omit to auto-generate
    auto_scale: bool = False


@app.post("/api/v1/demo/login")
async def demo_login(body: DemoLoginRequest) -> JSONResponse:
    profile = demo_profiles.authenticate(body.password)
    if profile is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password")

    if profile.get("auto_scale"):
        # Re-resolve window_days to whatever retention tier the archive
        # has actually reached right now, rather than the fixed value the
        # profile was created with -- this is the whole point of
        # auto_scale: the demo's loop length grows on its own as the
        # archive does, without anyone coming back to bump each profile.
        conn = _conn()
        try:
            _, span_days = _seed_ready(conn, LOOP_DAYS)
            tier, _next_tier = _current_tier(span_days)
            live_window_days = RETENTION_TIERS.get(tier)
            if live_window_days:
                profile["window_days"] = live_window_days
        finally:
            conn.close()

    token = demo_profiles.issue_session_token(profile)
    return JSONResponse({
        "session": token,
        "label": profile["label"],
        "window_days": profile["window_days"],
        "speed": profile["speed"],
        "auto_scale": profile.get("auto_scale", False),
        "expires_in_seconds": demo_profiles.SESSION_TTL_SECONDS,
    })


@app.post("/admin/demo/profiles")
async def admin_create_profile(body: DemoProfileCreateRequest,
                                _admin: None = Depends(_require_admin)) -> JSONResponse:
    window_days = RETENTION_TIERS.get(body.window)
    if window_days is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"window must be one of {list(RETENTION_TIERS.keys())}",
        )
    result = demo_profiles.create_profile(
        label=body.label, window_days=window_days, speed=body.speed,
        password=body.password, auto_scale=body.auto_scale,
    )
    return JSONResponse(result)


@app.get("/admin/demo/profiles")
async def admin_list_profiles(_admin: None = Depends(_require_admin)) -> JSONResponse:
    return JSONResponse({"profiles": demo_profiles.list_profiles()})


@app.delete("/admin/demo/profiles/{profile_id}")
async def admin_revoke_profile(profile_id: int,
                                _admin: None = Depends(_require_admin)) -> JSONResponse:
    ok = demo_profiles.revoke_profile(profile_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such active profile")
    return JSONResponse({"revoked": profile_id})
