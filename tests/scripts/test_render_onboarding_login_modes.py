"""Parity audit 2026-10-15: render-onboarding.sh had no codex case, so a Codex agent's pamphlet told
it "you do NOT log in". Every login mode the liveness switch knows gets its own text."""
from __future__ import annotations

import re
from pathlib import Path

SRC = (Path(__file__).resolve().parents[2] / "scripts/agent-segmentation/render-onboarding.sh").read_text()


def _cases():
    block = re.search(r'case "\$LOGIN_MODE" in(.*?)\nesac', SRC, re.S).group(1)
    return dict(re.findall(r"^\s*([a-z*]+)\)\s*LOGIN_TEXT=\"(.*?)\" ;;", block, re.M))


def test_every_login_mode_has_its_own_text():
    c = _cases()
    assert {"claude", "codex", "ssh", "*"} <= set(c)
    assert "codex login --device-auth" in c["codex"] and "14 days" in c["codex"] and "daemon" in c["codex"]
    assert "do NOT log in" not in c["codex"]
