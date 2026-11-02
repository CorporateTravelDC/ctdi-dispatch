"""2026-11-02: the runner side of withheld categories. The browser's httponly pass
cookie is forwarded to dispatch-web (trusted origin only, never in the demo), the
local receiver's aircraft go through dispatch-web's screen and are all withheld if
that call fails, and pairing sets the cookie httponly + SameSite=Strict. No
network: httpx is replaced. Identifiers are synthetic (hex abc9f0 / abc9f1)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from runner import main as rm  # noqa: E402

PASS = "ctdc_opsdesk_" + "A" * 32


def _request(cookie: str | None = None, client=("100.64.0.1", 1)) -> Request:
    headers = [(b"cookie", f"{rm.WITHHELD_COOKIE}={cookie}".encode())] if cookie else []
    return Request({"type": "http", "headers": headers, "method": "GET", "path": "/api/map/aircraft",
                    "client": client, "query_string": b""})


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body, self.headers = status, body or {}, {"content-type": "application/json"}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Client:
    def __init__(self, resp=None, boom=False, seen=None):
        self.resp, self.boom, self.seen = resp, boom, seen if seen is not None else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None, timeout=None):
        self.seen.update(url=url, json=json, headers=headers or {})
        if self.boom:
            raise RuntimeError("down")
        return self.resp


def test_pass_cookie_is_forwarded_only_for_a_trusted_live_caller():
    with patch.object(rm, "DEMO_MODE", False), patch.object(rm, "_is_trusted", lambda r: True):
        assert rm._dispatch_proxy_headers(_request(PASS), "api/v1/watchlist")[rm.WITHHELD_HEADER] == PASS
        assert rm.WITHHELD_HEADER not in rm._dispatch_proxy_headers(_request("not-a-token"), "api/v1/watchlist")
        assert rm.WITHHELD_HEADER not in rm._dispatch_proxy_headers(_request(), "api/v1/watchlist")
    with patch.object(rm, "DEMO_MODE", False), patch.object(rm, "_is_trusted", lambda r: False):
        assert rm.WITHHELD_HEADER not in rm._dispatch_proxy_headers(_request(PASS), "api/v1/watchlist")
    with patch.object(rm, "DEMO_MODE", True), patch.object(rm, "_is_trusted", lambda r: True):
        assert rm._withheld_pass(_request(PASS)) == ""


def test_local_screen_drops_withheld_and_fails_closed():
    ac = [{"hex": "abc9f0", "flight": "TST201 "}, {"hex": "abc9f1", "flight": "TST202 ", "r": "QLP02B"}]
    seen: dict = {}
    with patch.object(rm, "DEMO_MODE", False), patch.object(rm, "_is_trusted", lambda r: True), \
         patch("runner.main.httpx.AsyncClient", lambda: _Client(_Resp(body={"withhold": [0]}), seen=seen)):
        out = asyncio.run(rm._ladd_screen_local(_request(PASS), ac))
    assert out == [ac[1]]
    assert seen["url"].endswith("/api/v1/withheld/screen") and seen["headers"][rm.WITHHELD_HEADER] == PASS
    assert seen["json"]["items"][1] == {"hex": "abc9f1", "callsign": "TST202", "registration": "QLP02B"}
    with patch.object(rm, "DEMO_MODE", False), \
         patch("runner.main.httpx.AsyncClient", lambda: _Client(boom=True)):
        assert asyncio.run(rm._ladd_screen_local(_request(), ac)) == []
    with patch.object(rm, "DEMO_MODE", False), \
         patch("runner.main.httpx.AsyncClient", lambda: _Client(_Resp(status=503))):
        assert asyncio.run(rm._ladd_screen_local(_request(), ac)) == []


def test_pairing_sets_an_httponly_strict_cookie():
    resp = _Resp(body={"token": PASS, "expires_at": 4102444800.0, "device": "tablet"})
    with patch.object(rm, "DEMO_MODE", False), patch.object(rm, "_is_trusted", lambda r: True), \
         patch("runner.main.httpx.AsyncClient", lambda: _Client(resp)):
        r = TestClient(rm.app).post("/api/withheld/pair", json={"code": "x" * 24})
    assert r.status_code == 200 and "tablet" in r.json()["status"]
    cookie = r.headers["set-cookie"].lower()
    assert f"{rm.WITHHELD_COOKIE}=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert PASS not in r.text


def test_vessels_go_through_the_vessel_screen():
    seen: dict = {}
    vessels = [{"mmsi": "999000001", "name": "TEST HELD"}, {"mmsi": "999000002", "name": "TEST OPEN"}]
    with patch.object(rm, "DEMO_MODE", False), patch.object(rm, "_is_trusted", lambda r: True), \
         patch("runner.main.httpx.AsyncClient", lambda: _Client(_Resp(body={"withhold": [0]}), seen=seen)):
        out = asyncio.run(rm._vessel_screen(_request(PASS), vessels))
    assert out == [vessels[1]] and seen["json"]["category"] == "vessel"
    assert seen["json"]["items"][0] == {"mmsi": "999000001", "imo": None, "name": "TEST HELD", "callsign": None}


def test_vessel_imo_and_callsign_reach_the_screen():
    """2026-11-02: a vessel listed only by IMO or callsign must still be withheld, so both
    sources' IMO and callsign are carried into the screen (IMO 0 = none in AIS)."""
    local = rm._norm_vessel({"mmsi": 999000003, "imo": 9999991, "callsign": "TSTCS", "name": "X"}, "local")
    hub = rm._norm_vessel({"MMSI": 999000004, "IMO": 0, "CALLSIGN": "TSTCT", "NAME": "Y"}, "aishub.net")
    seen: dict = {}
    with patch.object(rm, "DEMO_MODE", False), patch.object(rm, "_is_trusted", lambda r: True), \
         patch("runner.main.httpx.AsyncClient", lambda: _Client(_Resp(body={"withhold": [0]}), seen=seen)):
        out = asyncio.run(rm._vessel_screen(_request(), [local, hub]))
    assert out == [hub]
    assert seen["json"]["items"][0]["imo"] == "9999991" and seen["json"]["items"][0]["callsign"] == "TSTCS"
    assert seen["json"]["items"][1]["imo"] is None and seen["json"]["items"][1]["callsign"] == "TSTCT"


def test_pairing_refuses_cross_origin_and_the_demo():
    with patch.object(rm, "DEMO_MODE", False), patch.object(rm, "_is_trusted", lambda r: True):
        r = TestClient(rm.app).post("/api/withheld/pair", json={"code": "x" * 24},
                                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 404
    with patch.object(rm, "DEMO_MODE", True):
        assert TestClient(rm.app).get("/api/withheld/pair").status_code in (401, 403, 404)
