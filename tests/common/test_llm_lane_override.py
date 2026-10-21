"""2026-10-20: CTDC_LLM_LANE=hot (set only by common/brief_lane.py after it took the
hot-lane unit lock) sends a report-tier persona straight to llama's second slot; without
it the persona still queues on the in-process long_runner_lock as before."""
from __future__ import annotations

import contextlib

import httpx
import pytest

from common import llama_pool, llm
from common.personas import PERSONAS


def _report_persona() -> str:
    return next(k for k, v in PERSONAS.items() if v["tier"] not in ("hot", "chat"))


@pytest.fixture
def wired(monkeypatch):
    key = _report_persona()
    posted, locked = [], []
    monkeypatch.setattr(llm, "_verify_before_inference", lambda m: None)
    monkeypatch.setattr("common.personas.persona_key_for", lambda m: key)
    monkeypatch.setattr("common.personas.build_system_prompt", lambda k: "sys")

    def post(url, json, timeout):
        posted.append(url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(llm.httpx, "post", post)

    @contextlib.contextmanager
    def lock(persona_key):
        locked.append(persona_key)
        yield llama_pool.LLAMA_PORT
    monkeypatch.setattr(llama_pool, "long_runner_lock", lock)
    return posted, locked


def test_hot_lane_override_skips_long_runner_lock(wired, monkeypatch):
    posted, locked = wired
    monkeypatch.setenv("CTDC_LLM_LANE", "hot")
    llm.ollama_post_with_retry({"model": "m", "prompt": "p"}, timeout=5)
    assert posted and locked == []


def test_without_override_report_persona_still_queues(wired, monkeypatch):
    posted, locked = wired
    monkeypatch.delenv("CTDC_LLM_LANE", raising=False)
    llm.ollama_post_with_retry({"model": "m", "prompt": "p"}, timeout=5)
    assert posted and len(locked) == 1


@pytest.mark.parametrize("v,expected", [("hot", "hot"), (" HOT ", "hot"), ("chat", None), ("", None), ("hott", None)])
def test_only_exact_hot_is_honoured(monkeypatch, v, expected):
    monkeypatch.setenv("CTDC_LLM_LANE", v)
    assert llm.lane_override() == expected
