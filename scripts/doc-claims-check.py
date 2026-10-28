#!/usr/bin/env python3
"""
scripts/doc-claims-check.py -- prose docs vs the live system, deterministically.

2026-10-16 (operator): the weekly doc drift check had stopped checking PROSE
docs against the box; it only ran the CLAUDE.md checker and listed units and
containers. This extracts, from README.md, docs/*.md (non-dated: no
YYYY-MM-DD in the filename) and skills/*/SKILL.md, every

  * repo path claim        -- `scripts/...`, `src/...`, `.config/...`, `docs/...`
  * unit claim             -- `corporatetraveldc-*.timer` / `.service`
  * schedule claim         -- `OnCalendar=...` / `OnUnitActiveSec=...` on the
                              same line as a `corporatetraveldc-*.timer` name

and verifies each against `git ls-files`, `systemctl --user list-unit-files`
(+ the system manager's), and the installed timer's own OnCalendar /
OnUnitActiveSec (`systemctl --user cat`, then `systemctl cat`). Struck text
(`~~...~~`, `<del>...</del>`) is superseded by definition and is skipped, as is
docs/RETIRED.md (it exists to name what is no longer live). A unit tracked
under scripts/agent-segmentation/units/ is installed in an agent account's
own user manager, which this account's `systemctl --user` cannot see, so it
is not reported; a SKILL.md path is also tried relative to its skill dir.

Output: one `[DRIFT] <doc>:<line>: ...` line per miss, then
`[SUMMARY] docs=N claims=M misses=K`. Exit 1 when K > 0, else 0. Stdlib only.

Usage:
  doc-claims-check.py                      # this repo, live facts from the box
  doc-claims-check.py --claims-only DIR    # README.md, docs/, skills/ under DIR
  doc-claims-check.py --live-facts FILE    # JSON {"tracked": [...], "units": [...],
                                           #       "timers": {"x.timer": {"OnCalendar": [...],
                                           #                              "OnUnitActiveSec": [...]}}}
                                           # instead of git/systemctl (tests)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DATED = re.compile(r"\d{4}-\d{2}-\d{2}")
STRIKE_INLINE = re.compile(r"~~.+?~~")
STRIKE_DEL = re.compile(r"<del>.*?</del>", re.S)

PATH_CLAIM = re.compile(r"(?<![\w/.~-])((?:scripts|src|\.config|docs)/[A-Za-z0-9_./@+-]*)")
UNIT_CLAIM = re.compile(r"(?<![\w/.-])(corporatetraveldc-[A-Za-z0-9_@%.-]*?\.(?:timer|service))(?![\w-])")
SCHEDULE_CLAIM = re.compile(r"(OnCalendar|OnUnitActiveSec)=([^`|;)\n]+)")
# 2026-10-26 (operator: catch the drift the docs sweep had to fix by hand): API routes a doc names
# must exist in the route code; "unit ... enabled|disabled" must match systemctl is-enabled.
ROUTE_CLAIM = re.compile(r"(?<![\w.:/-])(/api/[A-Za-z0-9_/{}<>.:-]+)")
ROUTE_SRC = re.compile(r"""["'](/api/[A-Za-z0-9_/{}<>.:-]+)["']""")
STATE_CLAIM = re.compile(r"\b(enabled|disabled)\b", re.I)
ROUTER_PREFIX = re.compile(r"""APIRouter\([^)]*prefix\s*=\s*["'](/api/[^"']*)["']""")
DECORATED_PATH = re.compile(r"""@\w+\.(?:get|post|put|patch|delete|api_route)\(\s*["']([^"']*)["']""")


def route_key(r: str) -> str:
    """Normalise a route for comparison: parameters ({x}, <x>, :x) become *, no trailing slash."""
    r = r.rstrip(".,;:)`'\"").rstrip("/")
    return "/".join("*" if re.fullmatch(r"\{[^}]*\}|<[^>]*>|:[A-Za-z_]\w*", s) else s for s in r.split("/"))
PATH_TRAIL = ".,;:)'\"]}>"
SKIP_DOCS = {"docs/RETIRED.md"}
AGENT_UNITS_DIR = "scripts/agent-segmentation/units/"


def _blank_keep_lines(m: re.Match) -> str:
    return "\n" * m.group(0).count("\n")


def strip_struck(text: str) -> str:
    text = STRIKE_DEL.sub(_blank_keep_lines, text)
    return "\n".join(STRIKE_INLINE.sub("", ln) for ln in text.split("\n"))


def collect_docs(root: Path) -> list[Path]:
    docs: list[Path] = []
    readme = root / "README.md"
    if readme.is_file():
        docs.append(readme)
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        docs += sorted(p for p in docs_dir.glob("*.md")
                       if not DATED.search(p.name)
                       and p.relative_to(root).as_posix() not in SKIP_DOCS)
    skills = root / "skills"
    if skills.is_dir():
        docs += sorted(skills.glob("*/SKILL.md"))
    return docs


def extract_claims(doc: Path, root: Path) -> list[tuple[str, int, str, str]]:
    """[(kind, line_no, value, extra)] -- kind in path|unit|schedule."""
    rel = doc.relative_to(root).as_posix()
    claims: list[tuple[str, int, str, str]] = []
    text = strip_struck(doc.read_text(encoding="utf-8", errors="replace"))
    # Fenced code is checked too: a command in a doc names real paths/units.
    for no, line in enumerate(text.split("\n"), 1):
        for m in PATH_CLAIM.finditer(line):
            end = m.end()
            if end < len(line) and line[end] in "*<{$":
                continue                                   # glob / placeholder
            p = m.group(1).rstrip(PATH_TRAIL)
            if any(ch in p for ch in "*<>{}$") or p.endswith("/."):
                continue
            claims.append(("path", no, p, rel))
        units_here = []
        for m in UNIT_CLAIM.finditer(line):
            u = m.group(1)
            if "%" in u and "@" not in u:
                continue
            claims.append(("unit", no, u, rel))
            if u.endswith(".timer"):
                units_here.append(u)
        for m in ROUTE_CLAIM.finditer(line):
            claims.append(("route", no, m.group(1), rel))
        all_units = [m.group(1) for m in UNIT_CLAIM.finditer(line)]
        st = STATE_CLAIM.findall(line)
        if len(all_units) == 1 and len({s.lower() for s in st}) == 1 and "@" not in all_units[0]:
            claims.append(("state", no, st[0].lower(), f"{rel}\t{all_units[0]}"))
        if len(units_here) == 1:
            for m in SCHEDULE_CLAIM.finditer(line):
                expr = " ".join(m.group(2).split()).rstrip(PATH_TRAIL + "*").strip()
                if not expr or expr.startswith("...") or "<" in expr:
                    continue
                claims.append(("schedule", no, f"{m.group(1)}={expr}", f"{rel}\t{units_here[0]}"))
    return claims


# ── live facts ──────────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


class LiveFacts:
    def __init__(self, root: Path, facts_file: Path | None):
        self.root = root
        self._timer_cache: dict[str, dict[str, list[str]]] = {}
        self.partial = False
        if facts_file and facts_file.name == "status.json":
            # 2026-10-26 (operator): an agent account (EA) runs the same check without seeing the
            # operator's user manager -- units come from the published status snapshot; enabled
            # state and timer schedules are not in the snapshot, so those claims are skipped.
            snap = json.loads(facts_file.read_text())
            self.tracked = set(_run(["git", "ls-files"], cwd=root).split("\n")) - {""}
            self.units = {u["unit"] for u in snap.get("units", [])} | {t["unit"] for t in snap.get("timers", [])}
            self.routes = set()
            facts_file = None
            self.synthetic = True
            self.partial = True
            self.states = {}
            self._load_routes(root)
            return
        if facts_file:
            data = json.loads(facts_file.read_text())
            self.tracked = set(data.get("tracked", []))
            self.units = set(data.get("units", []))
            self._timer_cache = {k: {kk: list(vv) for kk, vv in v.items()}
                                 for k, v in data.get("timers", {}).items()}
            self.routes = {route_key(r) for r in data.get("routes", [])}
            self.states = dict(data.get("states", {}))
            self.synthetic = True
        else:
            self.routes = set()
            self._load_routes(root)
            self.states = {}
            self.tracked = set(_run(["git", "ls-files"], cwd=root).split("\n")) - {""}
            units: set[str] = set()
            for scope in (["--user"], []):
                out = _run(["systemctl", *scope, "list-unit-files", "corporatetraveldc-*",
                            "--no-legend", "--plain"])
                units |= {ln.split()[0] for ln in out.splitlines() if ln.strip()}
            self.units = units
            self.synthetic = False

    def path_ok(self, p: str) -> bool:
        if p in self.tracked:
            return True
        prefix = p if p.endswith("/") else p + "/"
        return any(t.startswith(prefix) for t in self.tracked)

    def unit_ok(self, u: str) -> bool:
        if u in self.units or (AGENT_UNITS_DIR + u) in self.tracked:
            return True
        if "@" in u:                                        # instance -> template
            base, _, suffix = u.partition("@")
            return f"{base}@.{suffix.rsplit('.', 1)[-1]}" in self.units
        return False

    def _load_routes(self, root: Path) -> None:
        for f in (root / "src").rglob("*.py"):
            try:
                src = f.read_text(errors="replace")
            except OSError:
                continue
            self.routes |= {route_key(m.group(1)) for m in ROUTE_SRC.finditer(src)}
            # APIRouter(prefix="/api/v1/x") + @router.get("/y") -> /api/v1/x/y
            for pre in ROUTER_PREFIX.findall(src):
                self.routes |= {route_key(pre + p) for p in DECORATED_PATH.findall(src)}

    def route_ok(self, r: str) -> bool:
        tail = r.split("{", 1)[1] if "{" in r else ""
        if tail and ("}" not in tail or "," in tail.split("}", 1)[0]):
            r = r.split("{", 1)[0]                            # doc brace shorthand: /api/x/{a,b} -> /api/x
        if r.startswith("/api/dispatch/"):
            r = r[len("/api/dispatch"):]                      # the runner proxies /api/dispatch/<dispatch route>
            if r.count("/") <= 3 or "<" in r:
                return True                                   # the proxy itself or a placeholder
        k = route_key(r)
        if k in self.routes or any(s.startswith(k + "/") for s in self.routes):
            return True
        # a concrete example (/api/v1/fids/DCA/X1) matches a parameterised route (/api/v1/fids/*/*)
        parts = k.split("/")
        return any(len(s.split("/")) >= len(parts) and all(a == b or b == "*" for a, b in zip(parts, s.split("/")))
                   for s in self.routes)

    def state(self, unit: str) -> str:
        """'enabled' / 'disabled' / '' (static, masked, unknown: never a miss)."""
        if unit not in self.states and not self.synthetic:
            out = ""
            for scope in (["--user"], []):
                out = _run(["systemctl", *scope, "is-enabled", unit]).strip()
                if out in ("enabled", "disabled"):
                    break
            self.states[unit] = out if out in ("enabled", "disabled") else ""
        return self.states.get(unit, "")

    def timer(self, name: str) -> dict[str, list[str]]:
        if name in self._timer_cache:
            return self._timer_cache[name]
        found: dict[str, list[str]] = {"OnCalendar": [], "OnUnitActiveSec": []}
        if not self.synthetic:
            for scope in (["--user"], []):
                out = _run(["systemctl", *scope, "cat", name])
                if not out.strip():
                    continue
                for ln in out.splitlines():
                    ln = ln.strip()
                    for key in found:
                        if ln.startswith(key + "="):
                            found[key].append(" ".join(ln[len(key) + 1:].split()))
                break
        self._timer_cache[name] = found
        return found


def check(root: Path, facts: LiveFacts) -> tuple[list[str], int, int]:
    docs = collect_docs(root)
    drift: list[str] = []
    n_claims = 0
    seen: set[tuple[str, str]] = set()
    for doc in docs:
        for kind, no, value, extra in extract_claims(doc, root):
            n_claims += 1
            rel = extra.split("\t")[0]
            if kind == "path":
                ok = facts.path_ok(value)
                if not ok and rel.startswith("skills/"):       # skill-relative path
                    ok = facts.path_ok(rel.rsplit("/", 1)[0] + "/" + value)
                if not ok and (rel, value) not in seen:
                    seen.add((rel, value))
                    drift.append(f"[DRIFT] {rel}:{no}: path not tracked: {value}")
            elif kind == "unit":
                if facts.partial:
                    continue                                # snapshot lists loaded units only
                if not facts.unit_ok(value) and (rel, value) not in seen:
                    seen.add((rel, value))
                    drift.append(f"[DRIFT] {rel}:{no}: unit not installed: {value}")
            elif kind == "route":
                if not facts.route_ok(value) and (rel, value) not in seen:
                    seen.add((rel, value))
                    drift.append(f"[DRIFT] {rel}:{no}: route not in src/: {value}")
            elif facts.partial:
                continue                                    # snapshot mode: no enabled state, no schedules
            elif kind == "state":
                unit = extra.split("\t")[1]
                live = facts.state(unit)
                if live and live != value and (rel, unit + value) not in seen:
                    seen.add((rel, unit + value))
                    drift.append(f"[DRIFT] {rel}:{no}: {unit} documented {value}, live {live}")
            else:
                timer = extra.split("\t")[1]
                key, _, expr = value.partition("=")
                live = facts.timer(timer).get(key, [])
                if not facts.unit_ok(timer):
                    continue                                # already a unit miss
                if expr not in live:
                    drift.append(f"[DRIFT] {rel}:{no}: {timer} documents {key}={expr}; "
                                 f"live {key}={' / '.join(live) or '(none)'}")
    return drift, len(docs), n_claims


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--claims-only", metavar="DIR", help="root holding README.md, docs/, skills/")
    ap.add_argument("--live-facts", metavar="FILE", help="JSON facts instead of git/systemctl")
    args = ap.parse_args(argv)
    root = Path(args.claims_only or Path(__file__).resolve().parents[1]).resolve()
    if not root.is_dir():
        print(f"[FAIL] not a directory: {root}", file=sys.stderr)
        return 2
    facts = LiveFacts(root, Path(args.live_facts) if args.live_facts else None)
    drift, n_docs, n_claims = check(root, facts)
    for ln in drift:
        print(ln)
    print(f"[SUMMARY] docs={n_docs} claims={n_claims} misses={len(drift)}")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
