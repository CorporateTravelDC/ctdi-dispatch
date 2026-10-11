"""2026-10-10: composition skeleton -- compile, conflicts, expansion, forced-off delivery, state gate, core pin."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from common import composition as C

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "docs/deployment-profiles/compositions/emcomm-cert-hazard-operations.json"


def ex():
    return C.load(EXAMPLE)


def test_example_loads_validates_and_compiles_to_the_core_unchanged():
    comp = ex()
    eff = C.compile(comp)
    assert C.diff(comp["_core"], eff) == {}          # reference-only, all overlays disabled
    assert comp["state"] == "mapped" and not any(o["enabled"] for o in comp["overlays"])


def test_compile_is_deterministic_and_overlays_apply():
    comp = ex()
    comp["overlays"][0].update(enabled=True, settings={"alerts": {"nws": "on"}})
    a, b = C.compile(comp), C.compile(copy.deepcopy(comp))
    assert a == b and C.diff(comp["_core"], a) == {"alerts.nws": (None, "on")}


def test_two_overlays_setting_the_same_key_is_an_error():
    comp = ex()
    comp["overlays"][0].update(enabled=True, settings={"x": {"y": 1}})
    comp["overlays"][1].update(enabled=True, settings={"x": {"y": 2}})
    with pytest.raises(C.CompositionError, match="both set"):
        C.compile(comp)


def test_audience_expansion_is_rejected():
    comp = ex()
    comp["overlays"][0].update(enabled=True, audience_policy={"permitted_groups": ["subscribed_clients"]})
    with pytest.raises(C.CompositionError, match="expands audiences"):
        C.validate(comp)


@pytest.mark.parametrize("key", ["outward_delivery", "radio_tx"])
def test_outward_delivery_and_radio_must_stay_disabled(key):
    comp = ex()
    comp["escalation"][key] = "enabled"
    with pytest.raises(C.CompositionError, match=key):
        C.validate(comp)


@pytest.mark.parametrize("state", ["pilot", "active"])
def test_states_past_shadow_need_an_activation_record(state):
    comp = ex()
    comp["state"] = state
    with pytest.raises(C.CompositionError, match="activation record"):
        C.validate(comp)


def test_core_pin_mismatch_is_rejected(tmp_path):
    d = json.loads(EXAMPLE.read_text())
    d["core"]["sha256"] = "0" * 64
    d["core"]["path"] = str((EXAMPLE.parent / d["core"]["path"]).resolve())
    p = tmp_path / "c.json"; p.write_text(json.dumps(d))
    with pytest.raises(C.CompositionError, match="pin"):
        C.load(p)
