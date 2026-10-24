"""Composite retrieval rollout (geometric design Phase 2, 2026-10-22):
the two vault digests append the canonical all-angles block wrapped in the
markers the compile skips, never raise when the semantic layer fails, and
drop the block when the scrub gate blocks it."""
import os

import pytest

# webdav_client refuses to import without a named account (no silent default);
# these tests never reach Nextcloud.
os.environ.setdefault("NEXTCLOUD_ADMIN_USER", "test-user")

from second_brain import semantic  # noqa: E402
from second_brain.semantic.compile import ALL_ANGLES_BEGIN, ALL_ANGLES_END  # noqa: E402
from second_brain.scrub_gate import ScrubGateBlocked  # noqa: E402

SKILLS = ["poller.skills.disruption_weather_digest", "poller.skills.transport_pattern_digest"]


@pytest.mark.parametrize("modname", SKILLS)
def test_appendix_is_wrapped(monkeypatch, modname):
    mod = pytest.importorskip(modname)
    monkeypatch.setattr(semantic.compile, "all_angles_section", lambda: "GEOMETRY (x)\nline")
    out = mod._all_angles_appendix()
    assert out.startswith("## All angles (semantic layer)")
    assert ALL_ANGLES_BEGIN in out and out.rstrip().endswith(ALL_ANGLES_END)


@pytest.mark.parametrize("modname", SKILLS)
def test_appendix_empty_when_nothing_or_failure(monkeypatch, modname):
    mod = pytest.importorskip(modname)
    monkeypatch.setattr(semantic.compile, "all_angles_section", lambda: "")
    assert mod._all_angles_appendix() == ""

    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(semantic.compile, "all_angles_section", boom)
    assert mod._all_angles_appendix() == ""


@pytest.mark.parametrize("modname", SKILLS)
def test_appendix_dropped_when_scrub_gate_blocks(monkeypatch, modname):
    mod = pytest.importorskip(modname)
    monkeypatch.setattr(semantic.compile, "all_angles_section", lambda: "GEOMETRY (x)")

    def blocked(text, source=""):
        raise ScrubGateBlocked(["test"])
    monkeypatch.setattr(mod, "gate", blocked)
    assert mod._all_angles_appendix() == ""
