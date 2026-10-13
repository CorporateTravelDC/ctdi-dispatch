"""tests/runner/test_demo_gate.py -- 2026-10-12: in demo mode an untrusted visitor needs the demo session
for every /api read, and can never write or reach /api/ask; trusted (tailnet/LAN) requests are untouched."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from fastapi.testclient import TestClient  # noqa: E402

from runner import main as rm  # noqa: E402


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(rm, "DEMO_MODE", True)
    monkeypatch.setattr(rm, "_is_trusted", lambda request: request.headers.get("x-test-trusted") == "1")
    monkeypatch.setattr(rm, "_verify_demo_session", lambda tok: {"ok": True} if tok == "good" else None)
    return TestClient(rm.app)


def test_reads_need_the_demo_session(demo):
    assert demo.get("/api/v1/config").status_code == 401
    assert demo.get("/api/rss/user-feeds").status_code == 401
    assert demo.get("/api/chat/history").status_code == 401
    demo.cookies.set(rm.DEMO_SESSION_COOKIE, "good")
    assert demo.get("/api/v1/config").status_code != 401


def test_writes_and_ask_refused_even_with_a_session(demo):
    demo.cookies.set(rm.DEMO_SESSION_COOKIE, "good")
    assert demo.put("/api/v1/config", json={}).status_code == 403
    assert demo.delete("/api/chat/history").status_code == 403
    assert demo.post("/api/rss/user-feeds", json={}).status_code == 403
    assert demo.post("/api/ask", json={"message": "hi"}).status_code == 403


def test_open_paths_and_login_still_work(demo):
    assert demo.get("/healthz").status_code == 200
    assert demo.get("/api/demo/status").status_code != 401
    r = demo.post("/api/demo/login", json={"password": "x"})
    assert r.json().get("detail") != "Not available in the demo"


def test_trusted_requests_are_untouched(demo):
    h = {"x-test-trusted": "1"}
    c = TestClient(rm.app, raise_server_exceptions=False)    # the handler may hit the test DB; we only need it reached
    assert c.get("/api/v1/config", headers=h).status_code != 401
    assert c.delete("/api/chat/history", headers=h).status_code not in (401, 403)


def test_live_runner_is_unaffected(monkeypatch):
    monkeypatch.setattr(rm, "DEMO_MODE", False)
    monkeypatch.setattr(rm, "_is_trusted", lambda request: False)
    c = TestClient(rm.app)
    assert c.get("/api/demo/status").status_code != 403
    assert c.get("/api/rss/categories").status_code not in (401, 403)


# -- 2026-10-13: the trust rule the gate depends on, exactly as docs/dispatch-runner-design.md documents it --

def test_cf_connecting_ip_counts_only_with_the_public_host(monkeypatch):
    """CF-Connecting-IP is honored only when Host is a Cloudflare-fronted public hostname (2026-08-25 C-2).
    A box-side probe without that Host is a trusted loopback request -- why the 2026-10-13 relay probe
    without Host saw 200 while the gate was working."""
    monkeypatch.setattr(rm, "DEMO_MODE", True)
    c = TestClient(rm.app, raise_server_exceptions=False, client=("127.0.0.1", 50000))   # a probe from the box
    public = sorted(rm._CLOUDFLARE_FRONTED_HOSTNAMES)[0]
    visitor = {"CF-Connecting-IP": "203.0.113.9"}
    # loopback client + no public Host: trusted, the gate stays out of the way
    assert c.get("/api/v1/config", headers=visitor).status_code != 401
    # same request with the public Host: an untrusted public visitor
    assert c.get("/api/v1/config", headers={**visitor, "Host": public}).status_code == 401
    assert c.post("/api/ask", headers={**visitor, "Host": public}).status_code == 403
    assert c.get("/api/demo/status", headers={**visitor, "Host": public}).status_code not in (401, 403)
