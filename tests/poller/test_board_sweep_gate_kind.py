"""
tests/poller/test_board_sweep_gate_kind.py -- (operator 2026-10-16): the Cowork
board token-gate approval is kind "board-gate" with a TTL derived from the gate
deadline (governance.approval_ttl, COUNCIL_TTL_FRACTION of the remaining time
minus the 10 min buffer, clamped to (300, 7200)). It was kind "sudo" with the
whole remaining time as TTL; the 600 s sudo cap refused every 12 h lead in
governance.create_approval, so the gate never fired.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import poller.skills.board_sweep as bs  # noqa: E402
from common import governance as gov  # noqa: E402


@pytest.fixture
def gate_rig(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bs, "_GATE_STATE_FILE", str(tmp_path / "board-token-gate-state.json"))
    created, pushed = [], []

    def fake_create(pattern, command, *, kind, requester, reasoning, ttl_seconds):
        # the real check, so a refused TTL fails here exactly as it did live
        cap = gov.MAX_TTL_S[kind]
        if not (0 < float(ttl_seconds) <= cap):
            raise gov.GovernanceError(400, f"ttl_seconds for kind {kind} must be in (0, {cap}]")
        created.append({"pattern": pattern, "kind": kind, "requester": requester, "ttl": ttl_seconds})
        return {"id": f"apr-{len(created)}", "deny_key": "k"}

    monkeypatch.setattr(gov, "create_approval", fake_create)
    monkeypatch.setattr(bs, "_ntfy_gate_push", lambda *a, **k: pushed.append(a))
    # the resolution loop reads the request back the same sweep; still pending, nothing to post
    monkeypatch.setattr(bs.db, "get_approval_request", lambda rid: {"id": rid, "status": "pending"})
    monkeypatch.setattr(bs.db, "board_insert", lambda **kw: pytest.fail("no board reply while pending"))
    return created, pushed, tmp_path


def _gate_msg(deadline_epoch: float) -> dict:
    stamp = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(deadline_epoch))
    return {"id": "m-1", "thread": bs._TOKEN_GATE_THREAD, "from": "cowork",
            "subject": "token refresh", "body": f"Token is healthy (expires {stamp}, inside the window)"}


def test_12h_lead_fires_a_board_gate_approval_with_the_derived_ttl(gate_rig):
    created, pushed, tmp_path = gate_rig
    now = time.time()
    bs._handle_token_gate([_gate_msg(now + bs._GATE_LEAD_HOURS * 3600 - 1)])   # lead time just reached
    assert len(created) == 1
    assert created[0]["kind"] == "board-gate"
    assert created[0]["pattern"] == "cowork-board-token-gate" and created[0]["requester"] == "board:cowork"
    expected = gov.approval_ttl("board-gate", remaining_s=bs._GATE_LEAD_HOURS * 3600 - 1 - bs._GATE_DEADLINE_BUFFER_S)
    assert abs(created[0]["ttl"] - expected) < 2 and 4200 < created[0]["ttl"] < 4300
    assert created[0]["ttl"] <= gov.MAX_TTL_S["board-gate"]
    assert created[0]["ttl"] > gov.MAX_TTL_S["sudo"]            # this is the value the sudo cap refused
    state = json.loads((tmp_path / "board-token-gate-state.json").read_text())
    assert state["m-1"]["approval_request_id"] == "apr-1"
    assert len(pushed) == 1 and "UTC" in pushed[0][2] and "ET" in pushed[0][2]
    assert "deny_key" not in json.dumps(state) and "k=" not in pushed[0][2]


def test_unparseable_deadline_fires_now_at_the_floor(gate_rig):
    created, pushed, _ = gate_rig
    bs._handle_token_gate([{"id": "m-2", "thread": bs._TOKEN_GATE_THREAD, "from": "cowork",
                            "subject": "no deadline", "body": "please refresh"}])
    assert len(created) == 1 and created[0]["kind"] == "board-gate"
    assert created[0]["ttl"] == gov.APPROVAL_TTL_POLICY["board-gate"][1]   # 60 s remaining -> floor


def test_not_yet_inside_the_lead_does_not_fire(gate_rig):
    created, pushed, _ = gate_rig
    bs._handle_token_gate([_gate_msg(time.time() + (bs._GATE_LEAD_HOURS + 2) * 3600)])
    assert created == [] and pushed == []


def test_sudo_kind_is_gone_from_the_gate():
    src = (Path(__file__).resolve().parents[2] / "src/poller/skills/board_sweep.py").read_text()
    assert 'kind="sudo"' not in src and 'kind="board-gate"' in src
