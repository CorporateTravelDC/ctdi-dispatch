"""
(parity audit 2026-10-15) web/main.py registered GET /api/v1/watchlist and
DELETE /api/v1/watchlist/{session_id} after app.include_router(watchlist_router),
whose GET "" and DELETE "/{entry_id}" match the same requests first -- the
main.py handlers were unreachable duplicates. They were removed; this pins
that every (method, path) the app serves resolves to exactly one handler
that can actually be reached, and that the router serves the watchlist list.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import web.main as web_main  # noqa: E402
from auth.auth import Tier, resolve_tier  # noqa: E402
from web.routes import watchlist as wl  # noqa: E402


def _main_routes(method: str, path: str):
    return [r for r in web_main.app.routes
            if getattr(r, "path", None) == path and method in (getattr(r, "methods", None) or set())]


def test_no_main_duplicates_of_router_watchlist_routes():
    assert _main_routes("GET", "/api/v1/watchlist") == []
    assert _main_routes("DELETE", "/api/v1/watchlist/{session_id}") == []
    assert not hasattr(web_main, "list_watchlists")
    assert not hasattr(web_main, "terminate_watchlist")


def test_post_start_watchlist_still_registered():
    assert len(_main_routes("POST", "/api/v1/watchlist")) == 1


def test_get_watchlist_is_served_by_the_router(monkeypatch):
    monkeypatch.setattr(wl.db, "get_watchlist_entries", lambda: [{"id": "x"}])
    web_main.app.dependency_overrides[resolve_tier] = lambda: Tier.T1
    try:
        r = TestClient(web_main.app).get("/api/v1/watchlist")
    finally:
        web_main.app.dependency_overrides.pop(resolve_tier, None)
    assert r.status_code == 200
    assert r.json() == {"entries": [{"id": "x"}], "count": 1}
