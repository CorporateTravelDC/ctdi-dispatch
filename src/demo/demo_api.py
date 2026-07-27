"""
demo.demo_api — read-only playback API over the demo archive (demo.db).

Serves the same GET /api/v1/{endpoint} path shape as the live web app
(src/web/main.py), so an unmodified `runner` frontend/backend can point
its DISPATCH_BASE_URL at this service instead of the live one and get a
drop-in "demo mode" -- no runner code changes required.

Privacy boundary (hard, not cosmetic): this service NEVER reads "now".
It replays a fixed, looping window anchored to the oldest snapshot ever
recorded, so today's real operational data can never reach this path,
no matter how long the service runs or how the archive grows. See
_virtual_timestamp() for the one function that enforces this.

Loop design:
  - ANCHOR = earliest captured_at in the archive (fixed at first query,
    cached for process lifetime -- an archive only grows forward, so the
    anchor never needs to move).
  - LOOP_DAYS = 14, matching demo.recorder.SEED_TARGET (the point at
    which the archive is considered to have "enough" history for a
    believable demo cycle).
  - virtual_timestamp = ANCHOR + ((now - ANCHOR) mod LOOP_DAYS)
  - Each endpoint returns its most recent real snapshot at or before
    virtual_timestamp -- i.e. "what the platform actually showed" at
    that point in the archived history, replayed on an endless loop.

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
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse, Response

DEMO_DB   = os.environ.get("DEMO_DB", "/var/lib/corporatetraveldc/demo.db")
LOOP_DAYS = int(os.environ.get("DEMO_LOOP_DAYS", "14"))

# Same endpoint set demo.recorder captures -- keep in sync with
# demo.recorder.ENDPOINTS by design (both read from the same live API).
ENDPOINTS = [
    "tfr", "weather", "alerts", "cps", "notams",
    "amtrak", "opsplan", "route", "brief",
]

app = FastAPI(title="corporatetraveldc-demo-api")

_anchor_cache: datetime | None = None


def _conn() -> sqlite3.Connection:
    # Read-only URI connection -- this process must never write demo.db;
    # the recorder is the only writer, and mixing writers risks lock
    # contention against a service that also has a public-facing surface.
    return sqlite3.connect(f"file:{DEMO_DB}?mode=ro", uri=True)


def _anchor(conn: sqlite3.Connection) -> datetime | None:
    global _anchor_cache
    if _anchor_cache is not None:
        return _anchor_cache
    row = conn.execute("SELECT MIN(captured_at) FROM snapshots").fetchone()
    if not row or not row[0]:
        return None
    _anchor_cache = datetime.fromisoformat(row[0])
    return _anchor_cache


def _virtual_timestamp(conn: sqlite3.Connection) -> datetime | None:
    """The one function that decides what point in history to replay.

    Never touches real wall-clock "now" beyond computing elapsed time
    since a fixed historical anchor -- the result is always somewhere
    inside [ANCHOR, ANCHOR + LOOP_DAYS), never today's real date, unless
    the archive itself is younger than LOOP_DAYS (handled by the caller
    via the 503 seed-not-ready path).
    """
    anchor = _anchor(conn)
    if anchor is None:
        return None
    now = datetime.now(timezone.utc)
    elapsed = now - anchor
    loop_span = timedelta(days=LOOP_DAYS)
    offset = timedelta(seconds=elapsed.total_seconds() % loop_span.total_seconds())
    return anchor + offset


def _seed_ready(conn: sqlite3.Connection) -> tuple[bool, int]:
    anchor = _anchor(conn)
    if anchor is None:
        return False, 0
    newest_row = conn.execute("SELECT MAX(captured_at) FROM snapshots").fetchone()
    newest = datetime.fromisoformat(newest_row[0]) if newest_row and newest_row[0] else anchor
    span_days = (newest - anchor).total_seconds() / 86400
    return span_days >= LOOP_DAYS, int(span_days)


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
        return JSONResponse({
            "ready": ready,
            "loop_days": LOOP_DAYS,
            "archive_span_days": span_days,
            "total_snapshots": total,
            "mode": "demo-playback",
        })
    finally:
        conn.close()


def _load_snapshot(conn: sqlite3.Connection, endpoint: str, at: datetime) -> tuple[str, str] | None:
    """Most recent snapshot for `endpoint` at or before virtual timestamp `at`.
    Returns (payload_text, captured_at) or None."""
    row = conn.execute(
        "SELECT payload, payload_hash, compressed, captured_at FROM snapshots "
        "WHERE endpoint=? AND captured_at<=? ORDER BY captured_at DESC LIMIT 1",
        (endpoint, at.isoformat()),
    ).fetchone()
    if row is None:
        return None
    payload, _payload_hash, compressed, captured_at = row
    if compressed:
        text = zlib.decompress(payload).decode("utf-8", errors="replace")
    else:
        text = payload if isinstance(payload, str) else payload.decode("utf-8", errors="replace")
    return text, captured_at


def _endpoint_route(endpoint: str):
    async def _handler() -> Response:
        conn = _conn()
        try:
            ready, span_days = _seed_ready(conn)
            if not ready:
                return JSONResponse(
                    {
                        "error": "demo_seed_not_ready",
                        "detail": f"archive has {span_days}/{LOOP_DAYS} days of history",
                    },
                    status_code=503,
                )
            vt = _virtual_timestamp(conn)
            found = _load_snapshot(conn, endpoint, vt)
            if found is None:
                return JSONResponse(
                    {"error": "no_snapshot", "endpoint": endpoint}, status_code=404
                )
            text, captured_at = found
            return Response(
                content=text,
                media_type="application/json",
                headers={
                    "X-Demo-Mode": "true",
                    "X-Demo-Replayed-From": captured_at,
                },
            )
        finally:
            conn.close()

    return _handler


# Register one route per archived endpoint -- exact path parity with the
# live app (src/web/main.py's /api/v1/{name}) is the whole point: it lets
# an unmodified runner instance swap DISPATCH_BASE_URL and nothing else.
for _ep in ENDPOINTS:
    app.add_api_route(f"/api/v1/{_ep}", _endpoint_route(_ep), methods=["GET"])


@app.get("/api/v1/feeds")
async def feeds_stub() -> JSONResponse:
    """The runner's context builder also polls /api/v1/feeds for freshness
    display. The recorder doesn't archive this endpoint (it's a live-only
    health signal, not demo-relevant content), so this returns an honest
    static stub instead of a 404 the frontend would otherwise render as
    a broken widget."""
    return JSONResponse({"mode": "demo-playback", "note": "feed freshness not tracked in demo mode"})
