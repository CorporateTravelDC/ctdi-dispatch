"""2026-10-11: agent time card summary."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location("tc", REPO / "scripts/agent_timecard.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_shift_work_exceptions_and_signed_requests():
    m = load()
    G = "agent.tool.call"
    rows = [(1000.0, G, {"account": "ctdc-agent-x", "tool": "board_read", "outcome": "ok"}),
            (1600.0, G, {"account": "ctdc-agent-x", "tool": "workspace_contribute", "outcome": "ok"}),
            (2000.0, G, {"account": "ctdc-agent-x", "tool": "research_list", "outcome": "refused"}),
            (4600.0, "board.signed.request", {"signer": "ctdc-agent-y", "route": "POST /api/v1/workspace/contribute"}),
            (5000.0, G, {"account": "someone-else", "tool": "board_read", "outcome": "ok"})]
    c = m.summarize(rows, ["ctdc-agent-x", "ctdc-agent-y", "ctdc-agent-z"])
    x, y, z = c["ctdc-agent-x"], c["ctdc-agent-y"], c["ctdc-agent-z"]
    assert x["actions"] == 3 and x["span_h"] == round(1000 / 3600, 2) and x["contributions"] == 1
    assert [e["outcome"] for e in x["exceptions"]] == ["refused"]
    assert y["contributions"] == 1 and y["signed_requests"] == {"POST /api/v1/workspace/contribute": 1}
    assert z["active"] is False and z["actions"] == 0


def test_render_has_one_row_per_agent_and_no_content():
    m = load()
    c = m.summarize([], ["ctdc-agent-a", "ctdc-agent-b"])
    md = m.render("2026-10-10", c, "ctdi-20261011T034000Z")
    assert md.count("| ctdc-agent-") == 2 and "ctdi-20261011T034000Z" in md
