"""2026-10-18 (relay L1, panel phase 0, decision G2): POST /api/ask on the
production runner answers only a trusted (tailnet/LAN) caller -- the runner's
existing operator mechanism (_is_trusted), the same gate as /api/chat/history,
with the same 404. The demo refusal (127a2f76) is unchanged."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from fastapi.testclient import TestClient  # noqa: E402

from runner import main as rm  # noqa: E402


@pytest.fixture
def mocked(monkeypatch):
    calls = {"llm": 0, "saved": 0}

    async def ctx():
        return {"tfr": {"tfrs": [{"tfr_id": "5/1", "is_vip": True}], "count": 1}}

    async def llm(system, messages, model=None):
        calls["llm"] += 1
        calls["system"] = system
        yield f"data: {json.dumps({'type': 'text', 'text': 'ok'})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    monkeypatch.setattr(rm, "_build_context_rich", ctx)
    monkeypatch.setattr(rm, "_llm_stream", llm)
    monkeypatch.setattr(rm, "_chat_load_history", lambda limit=40: [])
    monkeypatch.setattr(rm, "_chat_save_exchange",
                        lambda u, a: calls.__setitem__("saved", calls["saved"] + 1))
    monkeypatch.setattr(rm, "_is_trusted", lambda request: request.headers.get("x-test-trusted") == "1")
    return calls


def test_untrusted_production_caller_is_refused(monkeypatch, mocked):
    monkeypatch.setattr(rm, "DEMO_MODE", False)
    r = TestClient(rm.app).post("/api/ask", json={"message": "tfr?"})
    assert r.status_code == 404
    assert mocked["llm"] == 0 and mocked["saved"] == 0


def test_demo_untrusted_still_refused_with_403(monkeypatch, mocked):
    monkeypatch.setattr(rm, "DEMO_MODE", True)
    monkeypatch.setattr(rm, "_verify_demo_session", lambda tok: {"ok": True})
    c = TestClient(rm.app)
    c.cookies.set(rm.DEMO_SESSION_COOKIE, "good")
    r = c.post("/api/ask", json={"message": "tfr?"})
    assert r.status_code == 403 and r.json()["detail"] == "Not available in the demo"
    assert mocked["llm"] == 0


def test_trusted_operator_reaches_the_model(monkeypatch, mocked):
    monkeypatch.setattr(rm, "DEMO_MODE", False)
    r = TestClient(rm.app).post("/api/ask", json={"message": "anything new?"},
                                headers={"x-test-trusted": "1"})
    assert r.status_code == 200
    assert '"text": "ok"' in r.text
    assert mocked["llm"] == 1 and mocked["saved"] == 1
    assert "TFRS: 1 active (1 VIP/POTUS)" in mocked["system"]
