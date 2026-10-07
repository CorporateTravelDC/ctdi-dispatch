#!/usr/bin/env python3
"""scripts/service-env/scan.py -- derive a service's SECRET env names by a
static import-closure scan (2026-10-05; how the <service>.allowlist files are
produced and re-checked).

  scan.py <service>            print NAME  # first consumer, for the allowlist
  scan.py --check              every allowlist vs a fresh scan: missing / extra

A name counts when it appears in getenv("X") / environ["X"] / environ.get("X")
/ config.get("X") / cfg("X") anywhere in the import closure of the service's
entry modules, AND is a name in the secrets source (NAMES are read from
dispatch-secrets.env; values are never read). Entry points per service are in
ENTRIES below -- they mirror the images' CMD / quadlet Exec lines.
Never prints a value.
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC = REPO / "src"
SECRETS = os.environ.get("SERVICE_ENV_SOURCE", "/etc/corporatetraveldc/dispatch-secrets.env")

# service -> entry modules / packages (a package means every module under it)
ENTRIES = {
    "web": ["web"],
    "poller": ["poller"],
    "pusher": ["pusher"],
    "runner": ["runner"],
    "ingest": ["ingest"],
    "amtrak-tracker": ["amtrak_tracker"],
    "acars-watcher": ["acars_watcher"],
    "demo": ["demo"],
    "execstandard-verifier": ["execstandard_verifier"],
}
_ENV_RE = re.compile(r"""(?:getenv|environ\.get|environ\[|config\.get|\bcfg|_env|env_get)\(?\s*["']([A-Z][A-Z0-9_]{2,})["']""")


def secret_names() -> set[str]:
    names = set()
    try:
        for line in Path(SECRETS).read_text().splitlines():
            m = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
            if m:
                names.add(m.group(1))
    except OSError as e:
        raise SystemExit(f"scan: cannot read secret NAMES from {SECRETS}: {e}")
    return names


def module_files(mod: str) -> list[Path]:
    base = SRC / Path(*mod.split("."))
    if base.is_dir():
        return sorted(base.rglob("*.py"))
    f = base.with_suffix(".py")
    return [f] if f.exists() else []


def imports_of(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(), str(path))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    out = set()
    pkg = ".".join(path.relative_to(SRC).with_suffix("").parts[:-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = pkg.split(".") if pkg else []
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(p for p in parts + ([base] if base else []) if p)
            out.add(base)
            out.update(f"{base}.{a.name}" for a in node.names)
    return out


def closure(entries: list[str]) -> list[Path]:
    seen: set[Path] = set()
    todo = [f for e in entries for f in module_files(e)]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for mod in imports_of(f):
            for g in module_files(mod):
                if g not in seen:
                    todo.append(g)
    return sorted(seen)


def scan(service: str, names: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for f in closure(ENTRIES[service]):
        for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
            for m in _ENV_RE.finditer(line):
                n = m.group(1)
                if n in names and n not in found:
                    found[n] = f"{f.relative_to(REPO)}:{i}"
    return found


def read_allowlist(path: Path) -> set[str]:
    return {ln.split("#", 1)[0].strip() for ln in path.read_text().splitlines() if ln.split("#", 1)[0].strip()}


def main(argv: list[str]) -> int:
    names = secret_names()
    if argv[:1] == ["--check"]:
        rc = 0
        for svc in ENTRIES:
            al = HERE / f"{svc}.allowlist"
            if not al.exists():
                print(f"{svc}: NO ALLOWLIST"); rc = 1; continue
            want, have = set(scan(svc, names)), read_allowlist(al)
            miss, extra = sorted(want - have), sorted(have - want)
            print(f"{svc}: {'ok' if not miss else 'MISSING ' + ' '.join(miss)}" + (f"  (extra, kept: {' '.join(extra)})" if extra else ""))
            rc |= bool(miss)
        return rc
    if len(argv) != 1 or argv[0] not in ENTRIES:
        print(__doc__); return 64
    for n, where in sorted(scan(argv[0], names).items()):
        print(f"{n}  # {where}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
