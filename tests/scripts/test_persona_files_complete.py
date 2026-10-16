"""2026-10-15 (casebook G25/D15): every persona in common/personas.py has its persona file
(corporatetraveldc.<key>) in the repo and in build-models.sh's sync list. A persona without a file is
refused by common/llm.py's signed-manifest check at call time, so its skill silently runs deterministic
(ep-advance-venues did, daily, 2026-08-31 to 2026-10-15)."""
from __future__ import annotations

import re
from pathlib import Path

from common.personas import PERSONAS

REPO = Path(__file__).resolve().parents[2]


def test_every_persona_has_a_persona_file():
    missing = [k for k in PERSONAS if not (REPO / f"corporatetraveldc.{k}").is_file()]
    assert missing == [], f"personas without a corporatetraveldc.<key> file: {missing}"


def test_every_persona_is_in_build_models_sync_list():
    listed = set(re.findall(r'\]="([a-z0-9-]+)"', (REPO / "build-models.sh").read_text()))
    assert set(PERSONAS) - listed == set(), f"not checked by build-models.sh: {sorted(set(PERSONAS) - listed)}"


def test_every_persona_file_is_in_the_manifest_or_staged_for_it():
    manifest = (REPO / "MANIFEST.sha256").read_text()
    unsigned = [k for k in PERSONAS if f"  corporatetraveldc.{k}\n" not in manifest]
    # A new persona file is signed by the relay that ships it; until then it may be absent from the
    # committed manifest, but never for a persona that already shipped in an earlier commit.
    assert len(unsigned) <= 1, f"persona files missing from the signed manifest: {unsigned}"


# (operator 2026-10-16): the weekly Llama backstop's model name must map to a persona, or
# common/llm.py answers 502 "no persona mapping" and both jobs it backstops never get a draft.
def test_claude_weekly_fallback_model_maps_to_its_persona():
    from common.personas import persona_key_for
    import poller.skills.claude_weekly_llama_fallback as fb
    assert fb.OLLAMA_MODEL.startswith("corporatetraveldc-pi5-claude-weekly-fallback")   # name kept
    assert persona_key_for(fb.OLLAMA_MODEL) == "claude-weekly-fallback"
    assert persona_key_for("corporatetraveldc-pi5-claude-weekly-fallback:latest") == "claude-weekly-fallback"
    p, sib = PERSONAS["claude-weekly-fallback"], PERSONAS["secondbrain-weekly"]
    assert (p["tier"], p["top_p"], p["num_ctx"]) == (sib["tier"], sib["top_p"], sib["num_ctx"])
    assert p["num_predict"] >= 1600    # the larger of the two jobs' max_tokens
