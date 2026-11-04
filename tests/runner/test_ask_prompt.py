"""tests/runner/test_ask_prompt.py -- Ask rebuild P0 (2026-11-03): /api/ask's system message is the
signed persona plus the signed glossary rules block plus the live data; the Ollama-era inline prompt
(retired callsign placeholder, "Ollama preferred") is gone."""
from __future__ import annotations

import re
from pathlib import Path

from runner import main as rm
from common import personas

SRC = Path(rm.__file__).read_text()


def test_ask_prompt_carries_signed_persona_and_glossary_rules(monkeypatch):
    monkeypatch.setattr(personas, "glossary_rules_block", lambda: "GLOSSARY-RULES-BLOCK")
    p = rm._ask_system_prompt(None, "CPS: 82 GO", "2026-11-03 15:00:00Z")
    assert personas.PERSONAS["chat"]["task"].split("\n", 1)[0] in p
    assert "GLOSSARY-RULES-BLOCK" in p
    assert "CPS: 82 GO" in p and "2026-11-03 15:00:00Z" in p
    assert "UTC with ET" in p


def test_unknown_model_falls_back_to_chat_persona(monkeypatch):
    monkeypatch.setattr(personas, "glossary_rules_block", lambda: "R")
    p = rm._ask_system_prompt("gemma3:4b", "", "now")
    assert personas.PERSONAS["chat"]["task"].split("\n", 1)[0] in p
    assert "No data available" in p


def test_retired_inline_prompt_is_gone():
    for retired in ("N0CALL", "Ollama preferred", "ARES NoVA District XX", "llama3.2:3b"):
        assert retired not in SRC


def test_drawer_has_no_retired_model_names():
    jsx = (Path(rm.__file__).parent / "frontend/src/components/DispatchDrawer.jsx").read_text()
    for retired in ("llama3.2", "llama3.1", "'mistral'"):
        assert retired not in jsx
    assert re.search(r"DEFAULT_MODEL = 'corporatetraveldc-pi5-chat'", jsx)
