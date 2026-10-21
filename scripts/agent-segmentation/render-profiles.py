#!/usr/bin/env python3
"""Pamphlet section: the deployment profiles, rendered from the profile files.

Everything is read from docs/deployment-profiles/ and src/common/profiles/ at
render time, plus a live count of the modules that import the profile loader,
so the pamphlet never claims more wiring than the code has. The private live
profile contributes only its name, base profile, modules and sources: its NOTAM
zones, agent scopes and integrations stay out, because cloud vendors read
these pamphlets.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PUBLIC_DIR = "docs/deployment-profiles/profiles"
PRIVATE_DIR = "docs/deployment-profiles/private"
REFERENCE = "src/common/profiles/ctdi-reference.json"
COMPOSITIONS = "docs/deployment-profiles/compositions"
LOADER_IMPORT = re.compile(r"^\s*(from\s+common(\.deployment_profile|\s+import\s+deployment_profile)|import\s+common\.deployment_profile)", re.M)


def _ls(xs) -> str:
    return ", ".join(f"`{x}`" for x in xs) if xs else "_(none)_"


def _profile_line(p: dict, name: str) -> str:
    org, dep = p.get("organization", {}), p.get("deployment", {})
    sec, wf = p.get("security", {}), p.get("workflows", {})
    parent = dep.get("profile")
    base = f" (extends `{parent}`)" if parent and parent not in (name, "CHANGE_ME") else ""
    return "\n".join([
        f"- **{name}**{base} -- industry `{org.get('industry', '?')}`, regulated: {str(org.get('regulated', False)).lower()}",
        f"  - modules: {_ls(p.get('modules', {}).get('enabled'))}",
        f"  - sources: {_ls(p.get('sources', {}).get('enabled'))}",
        f"  - human approvals: {_ls(wf.get('approvals'))}; always human-signed: {_ls(sec.get('human_signed_actions'))}",
        f"  - protected data classes: {_ls(sec.get('pii_classes'))}",
    ])


def _consumers(repo: Path) -> list[str]:
    out = []
    for f in sorted((repo / "src").rglob("*.py")):
        rel = f.relative_to(repo).as_posix()
        if rel == "src/common/deployment_profile.py":
            continue
        if LOADER_IMPORT.search(f.read_text(encoding="utf-8", errors="replace")):
            out.append(rel)
    return out


def render(repo: Path) -> str:
    consumers = _consumers(repo)
    lines = [
        "Design-stage contracts, not deployable configuration: one platform core, a profile per vertical "
        "(objects, workflows, modules, sources, human approval points, protected data), an explicit geography, "
        "per-profile source adapters. The typed loader is `src/common/deployment_profile.py` "
        "(`DISPATCH_PROFILE` selects one, else the reference; unknown keys are errors).",
        "",
        f"Runtime consumers of the loader today ({len(consumers)}, counted from the code at render time): "
        + (_ls(consumers) if consumers else "_(none)_")
        + ". Anything not listed still uses its built-in Washington, DC defaults. Never describe a profile "
        "as live, deployed or certified for a client; the pack's own rule is that a template is not a "
        "compliance claim.",
        "",
        "Vertical profiles (placeholders only -- `CHANGE_ME`, example ids, no coordinates):",
        "",
    ]
    for f in sorted((repo / PUBLIC_DIR).glob("*.json")):
        lines.append(_profile_line(json.loads(f.read_text(encoding="utf-8")), f.stem))
    ref = repo / REFERENCE
    if ref.is_file():
        lines += ["", "Reference and live:", "",
                  _profile_line(json.loads(ref.read_text(encoding="utf-8")), "ctdi-reference")
                  + "\n  - the public known-good default; reproduces today's DC behaviour exactly"]
    for f in sorted((repo / PRIVATE_DIR).glob("*.json")):
        p = json.loads(f.read_text(encoding="utf-8"))
        dep = p.get("deployment", {})
        lines.append(
            f"- **{f.stem}** (private, extends `{dep.get('profile', '?')}`) -- this deployment's own ledger; "
            f"never published to the public mirror.\n"
            f"  - modules: {_ls(p.get('modules', {}).get('enabled'))}\n"
            f"  - sources: {_ls(p.get('sources', {}).get('enabled'))}\n"
            f"  - its NOTAM zones, agent scopes and integrations are deliberately left out of this pamphlet")
    comps = sorted((repo / COMPOSITIONS).glob("*.json"))
    if comps:
        lines += ["", "Compositions (a core plus overlays across profiles; skeleton only, nothing reads them at runtime):", ""]
        for f in comps:
            c = json.loads(f.read_text(encoding="utf-8"))
            lines.append(f"- **{c.get('id', f.stem)}** v{c.get('version', '?')}, state `{c.get('state', '?')}`")
    lines += ["", "Authoritative: `docs/deployment-profiles/README.md` (the 12 production gates) and "
              "`docs/deployment-profiles/STAGED.md` (what is wired, what is next). Glossary terms per profile: "
              "`scripts/render-glossary.py --profile NAME`."]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    a = ap.parse_args()
    print(render(a.repo.resolve()), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
