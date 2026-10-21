"""2026-10-20: route-impact runs every 300 s; its model call is skipped while the DC inputs
(DC VIP TFR id/text/window + DC-area NAS program types) are unchanged since the last
successful narrative. National TFR/NAS churn no longer reaches the model."""
from __future__ import annotations

import json
import os
import types

import pytest

os.environ.setdefault("DISPATCH_DB_BACKEND", "sqlite")

from poller.skills import route_impact as ri  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    data = {
        "tfrs": [{"tfr_id": "6/0100", "is_vip": 1, "effective_start": 1, "effective_end": 2,
                  "raw_json": json.dumps({"facility": "ZDC", "description": "VIP movement"})}],
        "nas": [{"program_id": "a", "type": "MIT", "facility": "IAD"}],
    }
    calls = []
    monkeypatch.setattr(ri, "db", types.SimpleNamespace(
        get_active_tfrs=lambda: data["tfrs"], get_active_nas_programs=lambda: data["nas"],
        insert_route_narrative=lambda *a, **k: None))
    monkeypatch.setattr(ri, "check_gate", lambda name, inputs, force=False: ("new", "h"))
    monkeypatch.setattr(ri, "commit_gate", lambda *a, **k: None)
    monkeypatch.setattr(ri, "log_usage", lambda *a, **k: None)
    monkeypatch.setattr(ri._ntfy, "send", lambda *a, **k: None)

    def model(inputs):
        calls.append(ri._vip_user_message(inputs))
        return f"narrative {len(calls)}"
    monkeypatch.setattr(ri, "_call_ollama_vip", model)
    return data, calls


def test_national_churn_does_not_call_model(env):
    data, calls = env
    ri.main()
    data["tfrs"] = data["tfrs"] + [{"tfr_id": f"6/9{i}", "is_vip": 0, "raw_json": "{}"} for i in range(5)]
    data["nas"] = [{"program_id": f"n{i}", "type": "MIT", "facility": "IAD"} for i in range(9)] + \
                  [{"program_id": "e", "type": "GDP", "facility": "EWR"}]
    ri.main()
    assert len(calls) == 1


def test_dc_change_calls_model(env):
    data, calls = env
    ri.main()
    data["nas"] = data["nas"] + [{"program_id": "g", "type": "GDP", "facility": "DCA"}]
    ri.main()
    data["tfrs"] = [dict(data["tfrs"][0], raw_json=json.dumps({"facility": "ZDC", "description": "moved"}))]
    ri.main()
    assert len(calls) == 3


def test_prompt_carries_dc_nas_only(env):
    data, calls = env
    data["nas"] = [{"program_id": f"n{i}", "type": "MIT", "facility": "IAD"} for i in range(9)] + \
                  [{"program_id": "e", "type": "GDP", "facility": "EWR"}]
    ri.main()
    assert "EWR" not in calls[0] and calls[0].count("MIT at IAD") == 1


def test_force_bypasses_and_fallback_is_never_recorded(env, monkeypatch):
    data, calls = env
    monkeypatch.setattr(ri, "_call_ollama_vip", lambda inputs: calls.append("x") or None)
    ri.main()                                # model failed -> deterministic fallback, nothing recorded
    monkeypatch.setattr(ri, "_call_ollama_vip", lambda inputs: calls.append("y") or "ok")
    ri.main()                                # so the next run still calls the model
    ri.main(force=True)
    assert calls == ["x", "y", "y"]
