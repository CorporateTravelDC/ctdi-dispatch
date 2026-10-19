"""2026-10-19 (lean-out L2, F7): the llama council responder takes the shared long-runner
generation lock before drafting (bounded wait, skip the hour when the lane is busy), and an
identical failed draft is not retried hourly -- only when its inputs change or after 24 h."""
from __future__ import annotations

import fcntl
import importlib.util
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/llama-council-responder.py"
UNIT = Path(__file__).resolve().parents[2] / "systemd/system/corporatetraveldc-llama-council.service"


@pytest.fixture
def mod(tmp_path, monkeypatch):
    lock = tmp_path / "long-runner.lock"
    lock.write_text("")
    monkeypatch.setenv("LLAMA_LONG_RUNNER_LOCK", str(lock))
    monkeypatch.setenv("LLAMA_COUNCIL_LOCK_WAIT_S", "0")
    monkeypatch.setenv("STATE_DIRECTORY", str(tmp_path / "state"))
    spec = importlib.util.spec_from_file_location("llama_council_responder", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m.os, "getloadavg", lambda: (1.0, 1.0, 1.0))
    monkeypatch.setattr(sys, "argv", ["x"])
    convene = {"id": 7, "mode": "council", "subject": "a topic", "brief": "b",
               "participants": [{"account": m.ME}]}
    posted = []

    def signed(method, path_q, body=b""):
        if path_q.startswith("/api/v1/council"):
            return 200, {"councils": [convene]}
        if "research/list" in path_q:
            return 200, {"files": []}
        posted.append(body)
        return 201, {"path": "p"}
    monkeypatch.setattr(m, "signed", signed)
    m._convene, m._posted, m._lock = convene, posted, lock
    return m


def test_failed_identical_draft_not_retried(mod, monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "draft", lambda *a: calls.append(a) and None)
    assert mod.main() == 1
    assert mod.main() == 0          # same inputs: skipped, no model call
    assert len(calls) == 1
    mod._convene["brief"] = "changed brief"
    mod.main()                      # changed inputs retry at once
    assert len(calls) == 2


def test_failure_expires_after_retry_window(mod, monkeypatch):
    k = mod.draft_key("council", "s", "b", "")
    mod.record_failure(k, now=1000.0)
    assert mod.recently_failed(k, now=1000.0 + 3600)
    assert not mod.recently_failed(k, now=1000.0 + mod.RETRY_AFTER_S + 1)


def test_busy_lane_skips_the_hour(mod, monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "draft", lambda *a: calls.append(a) or "text")
    fd = os.open(str(mod._lock), os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert mod.main() == 0
    finally:
        os.close(fd)
    assert calls == [] and mod._posted == []


def test_draft_runs_under_the_lock_and_releases(mod, monkeypatch):
    held = []

    def draft(*a):
        fd = os.open(str(mod._lock), os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held.append(False)
        except BlockingIOError:
            held.append(True)
        finally:
            os.close(fd)
        return "text"
    monkeypatch.setattr(mod, "draft", draft)
    assert mod.main() == 0
    assert held == [True] and len(mod._posted) == 1
    fd = os.open(str(mod._lock), os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)   # released afterwards
    os.close(fd)


def test_unit_has_a_writable_state_directory():
    text = UNIT.read_text()
    assert "StateDirectory=ctdc-agent-llama-council" in text
    assert "ProtectSystem=strict" in text
