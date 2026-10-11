"""common/composition.py -- deployment COMPOSITIONS: one core profile plus overlays,
compiled to a single effective profile (2026-10-10, skeleton; spec:
docs/deployment-profiles/compositions/EMCOMM-HAZARD-COMPOSITION-SPEC.md §2-3).

Standard library only. NOTHING at runtime reads compositions yet: this module
loads, validates, compiles and diffs, with no I/O side effects beyond reading
the composition and core files it is given.

Rules (the spec's validation list):
  * precedence: core < overlays (listed order) < explicit composition fields;
    the SAME key set by two enabled overlays is an ERROR, never last-wins;
  * an overlay may not add audiences or permissions the core does not have;
  * outward_delivery and radio_tx must be "disabled";
  * a state past "shadow" needs a signed activation record (not implemented:
    always refused for now);
  * the core profile's SHA-256 must match the pin.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

STATES = ("discovered", "mapped", "validated", "staged", "shadow", "pilot", "active", "suspended", "retired")
_PAST_SHADOW = {"pilot", "active"}
_FORCED_DISABLED = ("outward_delivery", "radio_tx")


class CompositionError(ValueError):
    pass


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load(path, core_dir=None) -> dict:
    comp = json.loads(Path(path).read_text())
    comp["_core"] = _load_core(comp, Path(core_dir) if core_dir else Path(path).parent)
    return comp


def _load_core(comp: dict, base: Path) -> dict:
    core = comp.get("core") or {}
    cpath = (base / core["path"]).resolve() if "path" in core else None
    if not cpath or not cpath.is_file():
        raise CompositionError(f"core profile not found: {core.get('path')!r}")
    pin = core.get("sha256")
    if pin and _sha256_file(cpath) != pin:
        raise CompositionError("core profile SHA-256 does not match the pin")
    return json.loads(cpath.read_text())


def _flatten(d: dict, prefix=""):
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and v:
            yield from _flatten(v, key)
        else:
            yield key, v


def _set(d: dict, dotted: str, value):
    parts = dotted.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def validate(comp: dict) -> None:
    if comp.get("state") not in STATES:
        raise CompositionError(f"state must be one of {STATES}")
    if comp["state"] in _PAST_SHADOW:
        raise CompositionError(f"state {comp['state']!r} requires a signed activation record (not available yet)")
    for k in _FORCED_DISABLED:
        if (comp.get("escalation") or {}).get(k, "disabled") != "disabled":
            raise CompositionError(f"escalation.{k} must be 'disabled'")
    core = comp["_core"]
    core_audiences = set((core.get("audience_policy") or {}).get("permitted_groups") or [])
    seen: dict[str, str] = {}
    for ov in comp.get("overlays") or []:
        if not ov.get("id"):
            raise CompositionError("overlay without id")
        if not ov.get("enabled"):
            continue
        extra = set((ov.get("audience_policy") or {}).get("permitted_groups") or []) - core_audiences
        if extra:
            raise CompositionError(f"overlay {ov['id']} expands audiences beyond the core: {sorted(extra)}")
        for key, _ in _flatten(ov.get("settings") or {}):
            if key in seen:
                raise CompositionError(f"overlays {seen[key]} and {ov['id']} both set {key!r}")
            seen[key] = ov["id"]


def compile(comp: dict) -> dict:
    """Effective profile: core, then enabled overlays' settings, then explicit fields."""
    validate(comp)
    eff = copy.deepcopy(comp["_core"])
    for ov in comp.get("overlays") or []:
        if ov.get("enabled"):
            for key, v in _flatten(ov.get("settings") or {}):
                _set(eff, key, v)
    for key, v in _flatten(comp.get("explicit") or {}):
        _set(eff, key, v)
    return eff


def diff(before: dict, after: dict) -> dict:
    """{dotted_key: (before, after)} for every changed leaf. No side effects."""
    a, b = dict(_flatten(before)), dict(_flatten(after))
    return {k: (a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}
